#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
糖小助 - 独立的血糖监控Web应用
"""

import asyncio
import json
import os
import socket
import sqlite3
import time
import re
try:
    import tomllib
except ImportError:
    import tomli as tomllib
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple, Any
import threading
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formatdate
from contextlib import contextmanager
from functools import lru_cache, wraps
import aiohttp
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session
from flask_socketio import SocketIO, emit
from loguru import logger
import schedule as schedule_lib

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-change-this')
socketio = SocketIO(app, cors_allowed_origins="*")

def ai_retry_decorator(max_retries=3):
    """
    AI服务请求的重试装饰器
    
    Args:
        max_retries: 最大重试次数，默认为3次
    
    Returns:
        装饰器函数
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            retry_count = 0
            last_exception = None
            
            while retry_count <= max_retries:
                try:
                    result = await func(*args, **kwargs)
                    return result
                    
                except Exception as e:
                    last_exception = e
                    retry_count += 1
                    
                    # 记录详细的错误日志
                    logger.warning(f"AI请求失败，第{retry_count}次重试 - 函数: {func.__name__}, 错误: {str(e)}")
                    
                    if retry_count <= max_retries:
                        # 计算指数退避时间：1s, 2s, 4s
                        backoff_delay = 2 ** (retry_count - 1)
                        logger.info(f"等待{backoff_delay}秒后进行第{retry_count + 1}次重试...")
                        await asyncio.sleep(backoff_delay)
                    else:
                        logger.error(f"AI请求重试次数已达上限({max_retries}次)，函数: {func.__name__}, 最终错误: {str(e)}")
                        raise last_exception
                        
        return wrapper
    return decorator

class NightscoutWebMonitor:
    def __init__(self):
        self.config = self.load_config()
        self.init_database()
        self.setup_scheduler()
        self._connection_pool = {}
        self._pool_lock = threading.Lock()
        # 添加内存缓存
        self._cache = {}
        self._cache_lock = threading.Lock()
        self._cache_ttl = {}
        
    @contextmanager
    def get_db_connection(self):
        """数据库连接池管理器"""
        thread_id = threading.get_ident()
        conn_key = f"conn_{thread_id}"
        
        try:
            with self._pool_lock:
                if conn_key not in self._connection_pool:
                    self._connection_pool[conn_key] = sqlite3.connect(
                        self.get_database_path(), 
                        check_same_thread=False,
                        timeout=30.0
                    )
                    # 启用WAL模式以提高并发性能
                    self._connection_pool[conn_key].execute("PRAGMA journal_mode=WAL")
                    # 优化SQLite性能设置
                    self._connection_pool[conn_key].execute("PRAGMA synchronous=NORMAL")
                    self._connection_pool[conn_key].execute("PRAGMA cache_size=10000")
                    self._connection_pool[conn_key].execute("PRAGMA temp_store=MEMORY")
                    
                conn = self._connection_pool[conn_key]
            
            yield conn
            
        except Exception as e:
            logger.error(f"数据库连接错误: {e}")
            raise
        finally:
            # 不在这里关闭连接，保持连接池
            pass
            
    def cleanup_connections(self):
        """清理数据库连接池"""
        with self._pool_lock:
            for conn_key, conn in self._connection_pool.items():
                try:
                    conn.close()
                except:
                    pass
            self._connection_pool.clear()
            
    def _get_cache_key(self, prefix: str, *args) -> str:
        """生成缓存键"""
        return f"{prefix}:{':'.join(str(arg) for arg in args)}"
    
    def _is_cache_valid(self, key: str, ttl_seconds: int = 300) -> bool:
        """检查缓存是否有效"""
        if key not in self._cache_ttl:
            return False
        return time.time() - self._cache_ttl[key] < ttl_seconds
    
    def _set_cache(self, key: str, value, ttl_seconds: int = 300):
        """设置缓存"""
        with self._cache_lock:
            self._cache[key] = value
            self._cache_ttl[key] = time.time()
    
    def _get_cache(self, key: str, default=None):
        """获取缓存"""
        with self._cache_lock:
            if self._is_cache_valid(key):
                return self._cache[key]
            # 清理过期缓存
            if key in self._cache:
                del self._cache[key]
                del self._cache_ttl[key]
            return default
    
    def _clear_cache(self, pattern: str = None):
        """清理缓存"""
        with self._cache_lock:
            if pattern:
                keys_to_remove = [k for k in self._cache.keys() if pattern in k]
                for key in keys_to_remove:
                    if key in self._cache:
                        del self._cache[key]
                    if key in self._cache_ttl:
                        del self._cache_ttl[key]
            else:
                self._cache.clear()
                self._cache_ttl.clear()
        
    def load_config(self):
        """加载配置文件"""
        config_path = "config.toml"
        default_config = {
            "basic": {
                "enable": True,
                "timezone_offset": 8,
                "height_cm": 0,
                "weight_kg": 0,
                "body_fat_percentage": 0
            },
            "nightscout": {
                "api_url": "",
                "api_key": "",
                "timezone_offset": 8
            },
            "ai_config": {
                "api_url": "http://localhost:11434/v1/chat/completions",
                "api_key": "",
                "model_name": "llama3.1:8b",
                "timeout": 30
            },
            "schedule": {
                "analysis_times": ["10:00", "15:00", "21:00"],
                "enable_auto_analysis": True,
                "sync_interval_minutes": 10
            },
            "notification": {
                "enable_web_push": True,
                "enable_email": False
            },
            "email": {
                "smtp_server": "",
                "smtp_port": 587,
                "smtp_username": "",
                "smtp_password": "",
                "from_email": "",
                "to_emails": []
            },
            "auth": {
                "enable": False,
                "password": ""
            },
            "treatment_plan": {
                "medications": [],
                "insulin_enabled": False,
                "insulin_dosage": 0,
                "insulin_frequency": "",
                "insulin_custom_frequency": ""
            },
            "alert": {
                "high_glucose_threshold": 10.0,
                "low_glucose_threshold": 3.9,
                "enable_email_alerts": False,
                "enable_xxtui_alerts": False
            },
            "xxtui": {
                "api_key": "",
                "from": "Nightscout"
            },
            "database": {
                "path": "data/nightscout_data.db"
            }
        }
        
        try:
            if os.path.exists(config_path):
                with open(config_path, "rb") as f:
                    config = tomllib.load(f)
                # 合并默认配置
                for section, values in default_config.items():
                    if section not in config:
                        config[section] = values.copy()
                    else:
                        for key, value in values.items():
                            if key not in config[section]:
                                config[section][key] = value
                
                # 特别确保 treatment_plan 字段存在
                if "treatment_plan" not in config:
                    config["treatment_plan"] = default_config["treatment_plan"].copy()
                else:
                    for key, value in default_config["treatment_plan"].items():
                        if key not in config["treatment_plan"]:
                            config["treatment_plan"][key] = value
                
                # 特别确保 alert 字段存在
                if "alert" not in config:
                    config["alert"] = default_config["alert"].copy()
                else:
                    for key, value in default_config["alert"].items():
                        if key not in config["alert"]:
                            config["alert"][key] = value
                
                # 特别确保 xxtui 字段存在
                if "xxtui" not in config:
                    config["xxtui"] = default_config["xxtui"].copy()
                else:
                    for key, value in default_config["xxtui"].items():
                        if key not in config["xxtui"]:
                            config["xxtui"][key] = value
                
                return config
            else:
                return default_config.copy()
        except Exception as e:
            logger.error(f"加载配置文件失败: {e}")
            return default_config.copy()

    def get_database_path(self):
        """获取数据库文件路径，支持环境变量和配置文件"""
        # 1. 优先检查环境变量
        env_db_path = os.environ.get('NIGHTSCOUT_DB_PATH')
        if env_db_path:
            logger.info(f"使用环境变量中的数据库路径: {env_db_path}")
            return env_db_path
        
        # 2. 使用配置文件中的路径
        db_path = self.config.get("database", {}).get("path", "data/nightscout_data.db")
        
        # 3. 检查是否是Docker环境，如果是则使用Docker专用路径
        if os.path.exists("/.dockerenv"):
            docker_db_path = "/app/data/nightscout_data.db"
            # 如果在Docker环境中且配置路径不存在，尝试使用Docker路径
            if not os.path.exists(db_path) and os.path.exists(os.path.dirname(docker_db_path)):
                logger.info(f"Docker环境检测到，使用Docker数据库路径: {docker_db_path}")
                return docker_db_path
        
        logger.info(f"使用配置文件中的数据库路径: {db_path}")
        return db_path

    def save_config(self, config):
        """保存配置文件"""
        try:
            import toml
            config_path = "/app/config.toml"
            with open(config_path, "w", encoding="utf-8") as f:
                toml.dump(config, f)
            self.config = config
            logger.info(f"配置已保存到 {config_path}")
            return True
        except Exception as e:
            logger.error(f"保存配置文件失败: {e}")
            return False

    def init_database(self):
        """初始化数据库"""
        try:
            conn = sqlite3.connect(self.get_database_path())
            cursor = conn.cursor()
            
            # 创建血糖数据表 - 性能优化版本
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS glucose_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date_string TEXT NOT NULL,
                    shanghai_time TEXT,
                    sgv INTEGER NOT NULL,
                    direction TEXT,
                    trend INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(date_string)
                )
            """)
            
            # 创建性能优化索引
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_glucose_shanghai_time ON glucose_data(shanghai_time)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_glucose_date_string ON glucose_data(date_string)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_glucose_created_at ON glucose_data(created_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_glucose_sgv ON glucose_data(sgv)")

            # 创建治疗数据表 - 性能优化版本
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS treatment_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date_string TEXT NOT NULL,
                    shanghai_time TEXT,
                    event_type TEXT NOT NULL,
                    carbs REAL,
                    protein REAL,
                    fat REAL,
                    insulin REAL,
                    notes TEXT,
                    duration INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(date_string, event_type, carbs, protein, fat)
                )
            """)
            
            # 创建治疗数据性能优化索引
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_treatment_shanghai_time ON treatment_data(shanghai_time)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_treatment_event_type ON treatment_data(event_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_treatment_created_at ON treatment_data(created_at)")

            # 创建运动数据表 - 性能优化版本
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS activity_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date_string TEXT NOT NULL,
                    shanghai_time TEXT,
                    event_type TEXT,
                    duration INTEGER,
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(date_string, event_type, duration)
                )
            """)
            
            # 创建运动数据性能优化索引
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_activity_shanghai_time ON activity_data(shanghai_time)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_activity_event_type ON activity_data(event_type)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_activity_created_at ON activity_data(created_at)")

            # 创建指尖血糖数据表 - 性能优化版本
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS meter_data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date_string TEXT NOT NULL,
                    shanghai_time TEXT,
                    sgv INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(date_string)
                )
            """)
            
            # 创建指尖血糖数据性能优化索引
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_meter_shanghai_time ON meter_data(shanghai_time)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_meter_sgv ON meter_data(sgv)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_meter_created_at ON meter_data(created_at)")
            
            # 创建用户订阅表（用于Web推送）
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS web_subscriptions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    endpoint TEXT UNIQUE NOT NULL,
                    p256dh TEXT NOT NULL,
                    auth TEXT NOT NULL,
                    is_active BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 创建消息表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    is_read BOOLEAN DEFAULT 0,
                    is_favorite BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 创建预测结果表 - 性能优化版本
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS prediction_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    prediction_time TIMESTAMP NOT NULL,
                    predicted_glucose_mgdl REAL NOT NULL,
                    predicted_glucose_mmol REAL NOT NULL,
                    confidence_score REAL NOT NULL,
                    algorithm_used TEXT NOT NULL,
                    data_points_count INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 创建预测结果性能优化索引
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_prediction_time ON prediction_results(prediction_time)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_prediction_confidence ON prediction_results(confidence_score)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_prediction_created_at ON prediction_results(created_at)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_prediction_algorithm ON prediction_results(algorithm_used)")
            
            # 创建低血糖警报表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS hypoglycemia_alerts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alert_time TIMESTAMP NOT NULL,
                    predicted_glucose_mgdl REAL NOT NULL,
                    predicted_glucose_mmol REAL NOT NULL,
                    risk_level TEXT NOT NULL CHECK (risk_level IN ('HIGH', 'MEDIUM', 'LOW')),
                    alert_status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (alert_status IN ('ACTIVE', 'ACKNOWLEDGED', 'DISMISSED')),
                    acknowledged_at TIMESTAMP,
                    notification_sent BOOLEAN DEFAULT 0,
                    notification_time TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 创建用户警报配置表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_alert_config (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    high_risk_threshold_mgdl REAL DEFAULT 70,
                    medium_risk_threshold_mgdl REAL DEFAULT 80,
                    enable_predictions BOOLEAN DEFAULT 1,
                    enable_alerts BOOLEAN DEFAULT 1,
                    notification_methods TEXT DEFAULT 'web',
                    enable_email_alerts BOOLEAN DEFAULT 0,
                    enable_xxtui_alerts BOOLEAN DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 为现有数据库添加新字段（如果不存在）
            try:
                cursor.execute("ALTER TABLE user_alert_config ADD COLUMN enable_email_alerts BOOLEAN DEFAULT 0")
            except sqlite3.OperationalError as e:
                # 字段可能已存在，忽略错误
                if "duplicate column name" not in str(e).lower():
                    logger.warning(f"添加enable_email_alerts字段时出错: {e}")
            
            try:
                cursor.execute("ALTER TABLE user_alert_config ADD COLUMN enable_xxtui_alerts BOOLEAN DEFAULT 0")
            except sqlite3.OperationalError as e:
                # 字段可能已存在，忽略错误
                if "duplicate column name" not in str(e).lower():
                    logger.warning(f"添加enable_xxtui_alerts字段时出错: {e}")
            
            # 添加notification_time字段到hypoglycemia_alerts表
            try:
                cursor.execute("ALTER TABLE hypoglycemia_alerts ADD COLUMN notification_time TIMESTAMP")
            except sqlite3.OperationalError as e:
                if "duplicate column name" not in str(e).lower():
                    logger.warning(f"添加notification_time字段时出错: {e}")
            
            # 插入默认警报配置（如果不存在）
            cursor.execute("""
                INSERT OR IGNORE INTO user_alert_config (id, high_risk_threshold_mgdl, medium_risk_threshold_mgdl, enable_predictions, enable_alerts, notification_methods, enable_email_alerts, enable_xxtui_alerts)
                VALUES (1, 70, 80, 1, 1, 'web', 0, 0)
            """)
            
            conn.commit()
            conn.close()
            logger.info("数据库初始化完成")
            
        except Exception as e:
            logger.error(f"初始化数据库失败: {e}")

    def setup_scheduler(self):
        """设置定时任务"""
        if self.config["schedule"]["enable_auto_analysis"]:
            for time_str in self.config["schedule"]["analysis_times"]:
                schedule_lib.every().day.at(time_str).do(self.scheduled_analysis)
        
        # 设置数据同步任务
        sync_interval = self.config["schedule"]["sync_interval_minutes"]
        schedule_lib.every(sync_interval).minutes.do(self.scheduled_sync)
        
        # 启动调度器线程
        def run_scheduler():
            while True:
                schedule_lib.run_pending()
                time.sleep(1)
        
        scheduler_thread = threading.Thread(target=run_scheduler, daemon=True)
        scheduler_thread.start()

    def mg_dl_to_mmol_l(self, mg_dl: float) -> float:
        """将mg/dL转换为mmol/L"""
        return round(mg_dl / 18.0, 1)

    def utc_to_shanghai_time(self, utc_time_str: str) -> str:
        """将UTC时间字符串转换为配置的时区时间字符串"""
        try:
            if not utc_time_str:
                return ""

            if utc_time_str.endswith('Z'):
                if '.' in utc_time_str:
                    utc_dt = datetime.fromisoformat(utc_time_str[:-1]).replace(tzinfo=timezone.utc)
                else:
                    utc_dt = datetime.fromisoformat(utc_time_str[:-1]).replace(tzinfo=timezone.utc)
            else:
                utc_dt = datetime.fromisoformat(utc_time_str).replace(tzinfo=timezone.utc)

            # 从配置读取时区偏移，默认为UTC+8（北京时间）
            timezone_offset = 8  # 默认值
            try:
                if hasattr(self, 'config') and isinstance(self.config, dict):
                    if "basic" in self.config and isinstance(self.config["basic"], dict):
                        offset_config = self.config["basic"].get("timezone_offset", 8)
                        # 验证时区偏移值的有效性（-12到14之间）
                        if isinstance(offset_config, (int, float)) and -12 <= offset_config <= 14:
                            timezone_offset = offset_config
                        else:
                            logger.warning(f"无效的时区偏移配置: {offset_config}，使用默认值UTC+8")
                    else:
                        logger.warning("配置中缺少basic节，使用默认时区偏移UTC+8")
                else:
                    logger.warning("配置对象无效，使用默认时区偏移UTC+8")
            except Exception as config_error:
                logger.error(f"读取时区配置失败: {config_error}，使用默认值UTC+8")

            # 使用配置的时区偏移
            target_tz = timezone(timedelta(hours=timezone_offset))
            target_dt = utc_dt.astimezone(target_tz)
            return target_dt.strftime('%Y-%m-%d %H:%M:%S')

        except Exception as e:
            logger.error(f"时区转换失败: {utc_time_str}, 错误: {e}")
            return utc_time_str

    def parse_time_string(self, time_str: str) -> datetime:
        """
        统一的时间字符串解析函数，支持多种格式
        
        Args:
            time_str: 时间字符串，支持以下格式：
                     - ISO格式: 2025-08-13T08:20:00.000Z
                     - 数据库格式: 2025-08-13 08:20:00
        
        Returns:
            datetime: 解析后的datetime对象（无时区信息）
            
        Raises:
            ValueError: 当时间格式不支持时
        """
        if not time_str:
            raise ValueError("Empty time string")
        
        # ISO格式: 2025-08-13T08:20:00.000Z
        if 'T' in time_str:
            return datetime.fromisoformat(time_str.replace('Z', '+00:00')).astimezone().replace(tzinfo=None)
        
        # 数据库格式: 2025-08-13 08:20:00
        elif ' ' in time_str:
            return datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S')
        
        # 尝试其他可能的格式
        else:
            raise ValueError(f"Unsupported time format: {time_str}")

    def _generate_post_meal_glucose_info(self, glucose_data: List[Dict], treatment_data: List[Dict]) -> List[Dict]:
        """生成餐后血糖信息"""
        post_meal_info = []
        
        try:
            # 遍历血糖数据，寻找餐后血糖（餐后2小时内）
            for glucose_item in glucose_data:
                glucose_time = datetime.strptime(glucose_item['shanghai_time'], '%Y-%m-%d %H:%M:%S')
                glucose_value = self.mg_dl_to_mmol_l(glucose_item.get('sgv', 0))
                
                # 寻找2小时内的餐食记录
                for treatment_item in treatment_data:
                    if treatment_item.get('event_type') in ['Meal Bolus', 'Carb Correction']:
                        meal_time = datetime.strptime(treatment_item['shanghai_time'], '%Y-%m-%d %H:%M:%S')
                        time_diff = (glucose_time - meal_time).total_seconds() / 3600  # 小时
                        
                        # 餐后2小时内
                        if 0 <= time_diff <= 2:
                            post_meal_info.append({
                                'glucose_time': glucose_item['shanghai_time'],
                                'glucose_value': round(glucose_value, 1),
                                'meal_time': treatment_item['shanghai_time'],
                                'carbs': treatment_item.get('carbs', 0),
                                'protein': treatment_item.get('protein', 0),
                                'fat': treatment_item.get('fat', 0),
                                'event_type': treatment_item.get('event_type', ''),
                                'notes': treatment_item.get('notes', ''),
                                'time_diff_hours': round(time_diff, 1)
                            })
                            break  # 找到一个匹配的餐食就停止
                            
        except Exception as e:
            logger.error(f"生成餐后血糖信息失败: {e}")
            
        return post_meal_info

    def get_time_window_from_analysis_time(self, analysis_time: datetime = None) -> int:
        """
        根据分析时间确定时间窗口ID
        
        时间窗口定义:
        - 窗口1: 00:00-14:59 (凌晨至下午，专注空腹+早餐后血糖)
        - 窗口2: 15:00-20:59 (下午至晚上，专注午餐时间血糖模式)
        - 窗口3: 21:00-23:59 (夜间，专注晚餐反应+全天总结)
        
        Args:
            analysis_time: 分析时间，默认为当前时间
            
        Returns:
            int: 时间窗口ID (1, 2, 或 3)
        """
        try:
            if analysis_time is None:
                analysis_time = self._now_in_config_timezone()
            
            # 获取小时数用于判断时间窗口
            hour = analysis_time.hour
            
            # 根据时间窗口定义返回对应的ID
            if 0 <= hour <= 14:
                return 1  # 窗口1: 00:00-14:59
            elif 15 <= hour <= 20:
                return 2  # 窗口2: 15:00-20:59
            else:  # 21 <= hour <= 23
                return 3  # 窗口3: 21:00-23:59
                
        except Exception as e:
            logger.error(f"确定时间窗口失败: {e}")
            # 默认返回窗口1
            return 1

    def get_smart_data_range(self, analysis_time: datetime = None) -> Dict[str, Any]:
        """
        获取智能数据范围信息
        
        Args:
            analysis_time: 分析时间，默认为当前时间
        
        Returns:
            Dict[str, Any]: 包含数据范围信息的字典
                - start_time: 当天00:00时间
                - end_time: 当前分析时间
                - start_time_str: 开始时间字符串
                - end_time_str: 结束时间字符串
                - range_description: 时间范围描述
                - expected_duration_hours: 预期数据时长（小时）
                - analysis_time_str: 分析时间字符串
        """
        try:
            if analysis_time is None:
                analysis_time = self._now_in_config_timezone()
            
            # 计算当天的开始时间（00:00）
            start_time = analysis_time.replace(hour=0, minute=0, second=0, microsecond=0)
            end_time = analysis_time
            
            # 计算预期时长（小时）
            expected_duration_hours = (end_time - start_time).total_seconds() / 3600
            
            # 格式化时间字符串
            start_time_str = start_time.strftime("%H:%M")
            end_time_str = end_time.strftime("%H:%M")
            analysis_time_str = analysis_time.strftime("%Y-%m-%d %H:%M:%S")
            
            # 生成范围描述
            if expected_duration_hours < 1:
                range_description = f"00:00-{end_time_str}（数据较少）"
            elif expected_duration_hours < 4:
                range_description = f"00:00-{end_time_str}（早起时段）"
            elif expected_duration_hours < 12:
                range_description = f"00:00-{end_time_str}（上午时段）"
            elif expected_duration_hours < 18:
                range_description = f"00:00-{end_time_str}（下午时段）"
            else:
                range_description = f"00:00-{end_time_str}（全天时段）"
            
            return {
                "start_time": start_time,
                "end_time": end_time,
                "start_time_str": start_time_str,
                "end_time_str": end_time_str,
                "range_description": range_description,
                "expected_duration_hours": expected_duration_hours,
                "analysis_time_str": analysis_time_str
            }
            
        except Exception as e:
            logger.error(f"获取智能数据范围失败: {e}")
            # 返回默认值
            default_time = self._now_in_config_timezone()
            start_time = default_time.replace(hour=0, minute=0, second=0, microsecond=0)
            return {
                "start_time": start_time,
                "end_time": default_time,
                "start_time_str": "00:00",
                "end_time_str": default_time.strftime("%H:%M"),
                "range_description": "00:00-当前时间",
                "expected_duration_hours": default_time.hour,
                "analysis_time_str": default_time.strftime("%Y-%m-%d %H:%M:%S")
            }

    def get_dynamic_data_range(self, analysis_time: datetime = None) -> Dict[str, Any]:
        """
        获取动态数据范围信息
        
        Args:
            analysis_time: 分析时间，默认为当前时间
        
        Returns:
            Dict[str, Any]: 包含数据范围信息的字典
                - start_time: 当天00:00时间
                - end_time: 当前分析时间
                - start_time_str: 开始时间字符串
                - end_time_str: 结束时间字符串
                - range_description: 时间范围描述
                - expected_duration_hours: 预期数据时长（小时）
                - analysis_time_str: 分析时间字符串
                - timezone_info: 时区信息
        """
        try:
            timezone_offset = self._get_valid_timezone_offset()
            timezone_name = self._format_timezone_name(timezone_offset)

            if analysis_time is None:
                analysis_time = self._now_in_config_timezone()
            
            # 计算当天的开始时间（00:00）
            start_time = analysis_time.replace(hour=0, minute=0, second=0, microsecond=0)
            end_time = analysis_time
            
            # 计算预期时长（小时）
            expected_duration_hours = (end_time - start_time).total_seconds() / 3600
            
            # 格式化时间字符串
            start_time_str = start_time.strftime("%H:%M")
            end_time_str = end_time.strftime("%H:%M")
            analysis_time_str = analysis_time.strftime("%Y-%m-%d %H:%M:%S")
            
            # 生成动态范围描述
            if expected_duration_hours < 1:
                range_description = f"00:00-{end_time_str}（数据较少）"
            elif expected_duration_hours < 4:
                range_description = f"00:00-{end_time_str}（早起时段）"
            elif expected_duration_hours < 12:
                range_description = f"00:00-{end_time_str}（上午时段）"
            elif expected_duration_hours < 18:
                range_description = f"00:00-{end_time_str}（下午时段）"
            else:
                range_description = f"00:00-{end_time_str}（全天时段）"
            
            return {
                "start_time": start_time,
                "end_time": end_time,
                "start_time_str": start_time_str,
                "end_time_str": end_time_str,
                "range_description": range_description,
                "expected_duration_hours": expected_duration_hours,
                "analysis_time_str": analysis_time_str,
                "timezone_info": timezone_name,
                "current_time_for_ai": analysis_time.strftime("%Y-%m-%d %H:%M:%S") + f" ({timezone_name})"
            }
            
        except Exception as e:
            logger.error(f"获取动态数据范围失败: {e}")
            # 返回默认值
            default_time = self._now_in_config_timezone()
            start_time = default_time.replace(hour=0, minute=0, second=0, microsecond=0)
            timezone_offset = self._get_valid_timezone_offset()
            timezone_name = self._format_timezone_name(timezone_offset)
            return {
                "start_time": start_time,
                "end_time": default_time,
                "start_time_str": "00:00",
                "end_time_str": default_time.strftime("%H:%M"),
                "range_description": "00:00-当前时间",
                "expected_duration_hours": default_time.hour,
                "analysis_time_str": default_time.strftime("%Y-%m-%d %H:%M:%S"),
                "timezone_info": timezone_name,
                "current_time_for_ai": default_time.strftime("%Y-%m-%d %H:%M:%S") + f" ({timezone_name})"
            }

    def filter_data_by_time_window(self, data: List[Dict], time_window: int, data_type: str) -> List[Dict]:
        """
        根据时间窗口过滤数据
        
        Args:
            data: 原始数据列表
            time_window: 时间窗口ID (1, 2, 或 3)
            data_type: 数据类型 ("glucose", "treatment", "activity", "meter")
            
        Returns:
            List[Dict]: 过滤后的数据列表
        """
        try:
            if not data:
                return []
            
            # 治疗数据不过滤，返回全日数据
            if data_type == "treatment":
                logger.info("治疗数据不进行时间窗口过滤，返回全日数据")
                return data
            
            # 定义时间窗口的时间范围
            time_ranges = {
                1: (0, 14),     # 窗口1: 00:00-14:59
                2: (15, 20),    # 窗口2: 15:00-20:59
                3: (21, 23)     # 窗口3: 21:00-23:59
            }
            
            if time_window not in time_ranges:
                logger.warning(f"无效的时间窗口ID: {time_window}，返回全部数据")
                return data
            
            start_hour, end_hour = time_ranges[time_window]
            filtered_data = []
            
            for item in data:
                # 根据数据类型获取时间字段
                time_field = ""
                if data_type in ["glucose", "activity"]:
                    time_field = "shanghai_time"
                elif data_type == "meter":
                    time_field = "shanghai_time"
                
                if not time_field:
                    logger.warning(f"未知数据类型: {data_type}，返回全部数据")
                    return data
                
                time_str = item.get(time_field, "")
                if not time_str:
                    continue
                
                try:
                    # 解析上海时间
                    if len(time_str) >= 19:
                        dt = datetime.strptime(time_str[:19], '%Y-%m-%d %H:%M:%S')
                    else:
                        logger.warning(f"时间格式不正确: {time_str}")
                        continue
                    
                    hour = dt.hour
                    
                    # 检查是否在时间窗口范围内
                    if start_hour <= hour <= end_hour:
                        filtered_data.append(item)
                        
                except ValueError as e:
                    logger.warning(f"解析时间失败: {time_str}, 错误: {e}")
                    continue
            
            logger.info(f"时间窗口{time_window}过滤结果: {data_type}数据从{len(data)}条过滤为{len(filtered_data)}条")
            return filtered_data
            
        except Exception as e:
            logger.error(f"时间窗口数据过滤失败: {e}")
            return data

    def check_data_completeness(self, glucose_data: List[Dict], treatment_data: List[Dict], 
                               activity_data: List[Dict], meter_data: List[Dict], 
                               data_range: Dict[str, Any]) -> Dict[str, Any]:
        """
        检查数据完整性
        
        Args:
            glucose_data: 血糖数据
            treatment_data: 治疗数据
            activity_data: 活动数据
            meter_data: 指尖血糖数据
            data_range: 数据范围信息
        
        Returns:
            Dict[str, Any]: 数据完整性检查结果
                - is_complete: 数据是否完整
                - missing_time_ranges: 缺失的时间段列表
                - data_density: 数据密度分析
                - completeness_score: 完整性评分(0-100)
                - warnings: 警告信息列表
        """
        try:
            start_time = data_range["start_time"]
            end_time = data_range["end_time"]
            expected_duration_hours = data_range["expected_duration_hours"]
            
            # 合并所有数据进行分析
            all_data = []
            time_field_map = {
                "glucose": "shanghai_time",
                "treatment": "shanghai_time",
                "activity": "shanghai_time",
                "meter": "shanghai_time"
            }
            
            # 处理血糖数据
            for entry in glucose_data:
                time_str = entry.get(time_field_map["glucose"], "")
                if time_str and self._is_time_in_range(time_str, start_time, end_time):
                    all_data.append(("glucose", time_str))
            
            # 处理指尖血糖数据
            for entry in meter_data:
                time_str = entry.get(time_field_map["meter"], "")
                if time_str and self._is_time_in_range(time_str, start_time, end_time):
                    all_data.append(("meter", time_str))
            
            # 处理治疗数据（全日数据都纳入考虑）
            for entry in treatment_data:
                time_str = entry.get(time_field_map["treatment"], "")
                if time_str and self._is_time_in_range(time_str, start_time, end_time):
                    all_data.append(("treatment", time_str))
            
            # 处理活动数据
            for entry in activity_data:
                time_str = entry.get(time_field_map["activity"], "")
                if time_str and self._is_time_in_range(time_str, start_time, end_time):
                    all_data.append(("activity", time_str))
            
            # 分析数据密度
            data_density = self._analyze_data_density(all_data, start_time, end_time)
            
            # 对于动态数据范围，不检测缺失时间段（数据截止到当前时间是正常的）
            missing_time_ranges = []  # 清空缺失时间段列表
            
            # 计算完整性评分（基于现有数据，不考虑缺失时间段）
            completeness_score = self._calculate_completeness_score(
                all_data, expected_duration_hours, data_density, []
            )
            
            # 生成警告信息
            warnings = []
            if completeness_score < 70:
                warnings.append("数据完整性较低，分析结果可能不够准确")
            if data_density.get("glucose_density", 0) < 0.5:
                warnings.append("血糖数据密度较低，建议增加测量频率")
            
            # 对于动态数据范围，只要有一定数据就认为是完整的
            is_complete = completeness_score >= 50 and len(all_data) > 0
            
            return {
                "is_complete": is_complete,
                "missing_time_ranges": missing_time_ranges,
                "data_density": data_density,
                "completeness_score": completeness_score,
                "warnings": warnings,
                "total_data_points": len(all_data),
                "analysis_time_range": data_range["range_description"]
            }
            
        except Exception as e:
            logger.error(f"数据完整性检查失败: {e}")
            return {
                "is_complete": False,
                "missing_time_ranges": [],
                "data_density": {},
                "completeness_score": 0,
                "warnings": ["数据完整性检查失败"],
                "total_data_points": 0,
                "analysis_time_range": "未知"
            }

    def _is_time_in_range(self, time_str: str, start_time: datetime, end_time: datetime) -> bool:
        """检查时间是否在指定范围内"""
        try:
            if len(time_str) >= 19:
                dt = datetime.strptime(time_str[:19], '%Y-%m-%d %H:%M:%S')
            else:
                return False
            
            return start_time <= dt <= end_time
        except (ValueError, TypeError):
            return False

    def _analyze_data_density(self, all_data: List[Tuple[str, str]], start_time: datetime, end_time: datetime) -> Dict[str, Any]:
        """分析数据密度"""
        try:
            # 按数据类型分组
            data_by_type = {}
            for data_type, time_str in all_data:
                if data_type not in data_by_type:
                    data_by_type[data_type] = []
                data_by_type[data_type].append(time_str)
            
            # 计算每种数据类型的密度
            density_info = {}
            for data_type, time_list in data_by_type.items():
                if len(time_list) == 0:
                    density_info[f"{data_type}_density"] = 0
                    density_info[f"{data_type}_count"] = 0
                    continue
                
                # 计算时间范围（小时）
                duration_hours = (end_time - start_time).total_seconds() / 3600
                if duration_hours == 0:
                    density_info[f"{data_type}_density"] = 0
                else:
                    # 计算每小时的平均数据点数
                    density = len(time_list) / duration_hours
                    density_info[f"{data_type}_density"] = density
                    density_info[f"{data_type}_count"] = len(time_list)
            
            # 计算整体密度
            density_info["overall_density"] = len(all_data) / max(1, (end_time - start_time).total_seconds() / 3600)
            density_info["overall_count"] = len(all_data)
            
            return density_info
            
        except Exception as e:
            logger.error(f"数据密度分析失败: {e}")
            return {"overall_density": 0, "overall_count": 0}

    def _detect_missing_time_ranges(self, all_data: List[Tuple[str, str]], start_time: datetime, end_time: datetime) -> List[Dict[str, Any]]:
        """检测缺失的时间段"""
        try:
            if not all_data:
                return [{
                    "start": start_time.strftime("%H:%M"),
                    "end": end_time.strftime("%H:%M"),
                    "duration_hours": (end_time - start_time).total_seconds() / 3600,
                    "severity": "high"
                }]
            
            # 提取所有时间点并排序
            time_points = []
            for data_type, time_str in all_data:
                try:
                    if len(time_str) >= 19:
                        dt = datetime.strptime(time_str[:19], '%Y-%m-%d %H:%M:%S')
                        time_points.append(dt)
                except ValueError:
                    continue
            
            if not time_points:
                return [{
                    "start": start_time.strftime("%H:%M"),
                    "end": end_time.strftime("%H:%M"),
                    "duration_hours": (end_time - start_time).total_seconds() / 3600,
                    "severity": "high"
                }]
            
            time_points.sort()
            
            # 检测缺失的时间段
            missing_ranges = []
            expected_interval = timedelta(minutes=30)  # 预期的数据间隔
            
            # 检查开始时间到第一个数据点
            if time_points[0] > start_time + expected_interval:
                missing_ranges.append({
                    "start": start_time.strftime("%H:%M"),
                    "end": time_points[0].strftime("%H:%M"),
                    "duration_hours": (time_points[0] - start_time).total_seconds() / 3600,
                    "severity": "high" if (time_points[0] - start_time) > timedelta(hours=2) else "medium"
                })
            
            # 检查数据点之间的间隔
            for i in range(len(time_points) - 1):
                gap = time_points[i + 1] - time_points[i]
                if gap > expected_interval:
                    missing_ranges.append({
                        "start": time_points[i].strftime("%H:%M"),
                        "end": time_points[i + 1].strftime("%H:%M"),
                        "duration_hours": gap.total_seconds() / 3600,
                        "severity": "high" if gap > timedelta(hours=2) else "medium"
                    })
            
            # 检查最后一个数据点到结束时间
            if time_points[-1] < end_time - expected_interval:
                missing_ranges.append({
                    "start": time_points[-1].strftime("%H:%M"),
                    "end": end_time.strftime("%H:%M"),
                    "duration_hours": (end_time - time_points[-1]).total_seconds() / 3600,
                    "severity": "high" if (end_time - time_points[-1]) > timedelta(hours=2) else "medium"
                })
            
            return missing_ranges
            
        except Exception as e:
            logger.error(f"缺失时间段检测失败: {e}")
            return []

    def _calculate_completeness_score(self, all_data: List[Tuple[str, str]], expected_duration_hours: float, 
                                    data_density: Dict[str, Any], missing_time_ranges: List[Dict[str, Any]]) -> float:
        """计算完整性评分"""
        try:
            score = 100.0
            
            # 基于数据密度的扣分
            overall_density = data_density.get("overall_density", 0)
            if overall_density < 1:  # 每小时少于1个数据点
                score -= 30
            elif overall_density < 2:  # 每小时少于2个数据点
                score -= 15
            elif overall_density < 4:  # 每小时少于4个数据点
                score -= 5
            
            # 基于血糖数据密度的扣分（血糖数据最重要）
            glucose_density = data_density.get("glucose_density", 0)
            if glucose_density < 1:
                score -= 40
            elif glucose_density < 2:
                score -= 20
            elif glucose_density < 4:
                score -= 10
            
            # 基于缺失时间段的扣分
            total_missing_hours = sum(missing_range.get("duration_hours", 0) for missing_range in missing_time_ranges)
            if expected_duration_hours > 0:
                missing_ratio = total_missing_hours / expected_duration_hours
                if missing_ratio > 0.5:  # 超过50%的时间缺失
                    score -= 50
                elif missing_ratio > 0.3:  # 超过30%的时间缺失
                    score -= 30
                elif missing_ratio > 0.1:  # 超过10%的时间缺失
                    score -= 15
            
            # 基于缺失时间段严重程度的扣分
            high_severity_count = sum(1 for missing_range in missing_time_ranges if missing_range.get("severity") == "high")
            if high_severity_count > 0:
                score -= min(20, high_severity_count * 10)
            
            # 确保分数在0-100之间
            return max(0, min(100, score))
            
        except Exception as e:
            logger.error(f"完整性评分计算失败: {e}")
            return 0.0

    def filter_data_by_smart_range(self, data: List[Dict], data_range: Dict[str, Any], 
                                  data_type: str) -> List[Dict]:
        """
        根据智能数据范围过滤数据
        
        Args:
            data: 原始数据列表
            data_range: 智能数据范围信息
            data_type: 数据类型 ("glucose", "treatment", "activity", "meter")
        
        Returns:
            List[Dict]: 过滤后的数据列表
        """
        try:
            if not data:
                return []
            
            # 治疗数据不过滤，返回全日数据（与原有逻辑保持一致）
            if data_type == "treatment":
                logger.info("治疗数据不进行智能范围过滤，返回全日数据")
                return data
            
            start_time = data_range["start_time"]
            end_time = data_range["end_time"]
            
            # 根据数据类型获取时间字段
            time_field_map = {
                "glucose": "shanghai_time",
                "activity": "shanghai_time", 
                "meter": "shanghai_time"
            }
            
            time_field = time_field_map.get(data_type)
            if not time_field:
                logger.warning(f"未知数据类型: {data_type}，返回全部数据")
                return data
            
            filtered_data = []
            
            for item in data:
                time_str = item.get(time_field, "")
                if not time_str:
                    continue
                
                try:
                    # 解析时间
                    if len(time_str) >= 19:
                        dt = datetime.strptime(time_str[:19], '%Y-%m-%d %H:%M:%S')
                    else:
                        logger.warning(f"时间格式不正确: {time_str}")
                        continue
                    
                    # 检查是否在智能数据范围内
                    if start_time <= dt <= end_time:
                        filtered_data.append(item)
                        
                except ValueError as e:
                    logger.warning(f"解析时间失败: {time_str}, 错误: {e}")
                    continue
            
            logger.info(f"智能数据范围过滤结果: {data_type}数据从{len(data)}条过滤为{len(filtered_data)}条")
            logger.info(f"智能数据范围: {data_range['range_description']}")
            
            return filtered_data
            
        except Exception as e:
            logger.error(f"智能数据范围过滤失败: {e}")
            return data

    def filter_data_by_dynamic_range(self, data: List[Dict], data_range: Dict[str, Any], 
                                   data_type: str) -> List[Dict]:
        """
        根据动态数据范围过滤数据
        
        Args:
            data: 原始数据列表
            data_range: 动态数据范围信息
            data_type: 数据类型 ("glucose", "treatment", "activity", "meter")
        
        Returns:
            List[Dict]: 过滤后的数据列表
        """
        try:
            if not data:
                return []
            
            # 治疗数据和血糖数据都不过滤，返回全日数据
            # 修复：血糖数据也应该返回全日数据，否则AI能看到治疗记录但看不到对应血糖数据
            if data_type in ["treatment", "glucose"]:
                logger.info(f"{data_type}数据不进行动态范围过滤，返回全日数据")
                return data
            
            start_time = data_range["start_time"]
            end_time = data_range["end_time"]
            
            # 根据数据类型获取时间字段
            time_field_map = {
                "glucose": "shanghai_time",
                "activity": "shanghai_time", 
                "meter": "shanghai_time"
            }
            
            time_field = time_field_map.get(data_type)
            if not time_field:
                logger.warning(f"未知数据类型: {data_type}，返回全部数据")
                return data
            
            filtered_data = []
            
            for item in data:
                time_str = item.get(time_field, "")
                if not time_str:
                    continue
                
                try:
                    # 解析时间
                    if len(time_str) >= 19:
                        dt = datetime.strptime(time_str[:19], '%Y-%m-%d %H:%M:%S')
                    else:
                        logger.warning(f"时间格式不正确: {time_str}")
                        continue
                    
                    # 检查是否在动态数据范围内
                    if start_time <= dt <= end_time:
                        filtered_data.append(item)
                        
                except ValueError as e:
                    logger.warning(f"解析时间失败: {time_str}, 错误: {e}")
                    continue
            
            logger.info(f"动态数据范围过滤结果: {data_type}数据从{len(data)}条过滤为{len(filtered_data)}条")
            logger.info(f"动态数据范围: {data_range['range_description']}")
            
            return filtered_data
            
        except Exception as e:
            logger.error(f"动态数据范围过滤失败: {e}")
            return data

    async def fetch_nightscout_data(self, start_date: str, end_date: str) -> Tuple[List[Dict], List[Dict], List[Dict], List[Dict]]:
        """从Nightscout获取指定时间范围的数据"""
        try:
            entries_url = f"{self.config['nightscout']['api_url']}/api/v1/entries.json"
            treatments_url = f"{self.config['nightscout']['api_url']}/api/v1/treatments.json"
            activity_url = f"{self.config['nightscout']['api_url']}/api/v1/activity.json"
            meter_url = f"{self.config['nightscout']['api_url']}/api/v1/meter.json"

            start_dt = datetime.strptime(start_date, '%Y-%m-%d')
            end_dt = datetime.strptime(end_date, '%Y-%m-%d')

            start_utc = start_dt - timedelta(hours=8)
            end_utc = end_dt + timedelta(days=1) - timedelta(hours=8)

            start_utc_str = start_utc.strftime('%Y-%m-%dT%H:%M:%S.000Z')
            end_utc_str = end_utc.strftime('%Y-%m-%dT%H:%M:%S.999Z')

            params = {
                "find[dateString][$gte]": start_utc_str,
                "find[dateString][$lte]": end_utc_str,
                "count": "10000",
                "sort$descending": "1"
            }

            treatment_params = {
                "find[created_at][$gte]": start_utc_str,
                "find[created_at][$lte]": end_utc_str,
                "count": "10000",
                "sort$descending": "1"
            }

            headers = {}
            if self.config['nightscout']['api_key']:
                headers["api-secret"] = self.config['nightscout']['api_key']

            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
                async with session.get(entries_url, params=params, headers=headers) as response:
                    if response.status == 200:
                        glucose_data = await response.json()
                        logger.info(f"获取到 {len(glucose_data)} 条血糖数据")
                    else:
                        logger.error(f"获取血糖数据失败: {response.status}")
                        glucose_data = []

                async with session.get(treatments_url, params=treatment_params, headers=headers) as response:
                    if response.status == 200:
                        treatment_data = await response.json()
                        logger.info(f"获取到 {len(treatment_data)} 条治疗数据")
                    else:
                        logger.error(f"获取治疗数据失败: {response.status}")
                        treatment_data = []

                # 尝试从 activity.json 获取运动数据
                try:
                    async with session.get(activity_url, params=treatment_params, headers=headers) as response:
                        if response.status == 200:
                            activity_data = await response.json()
                            logger.info(f"获取到 {len(activity_data)} 条运动数据")
                        else:
                            logger.warning(f"获取运动数据失败: {response.status}，将从治疗数据中识别")
                            activity_data = []
                except Exception as e:
                    logger.warning(f"获取运动数据异常: {e}，将从治疗数据中识别")
                    activity_data = []

                # 尝试从 meter.json 获取指尖血糖数据
                try:
                    async with session.get(meter_url, params=params, headers=headers) as response:
                        if response.status == 200:
                            meter_data = await response.json()
                            logger.info(f"获取到 {len(meter_data)} 条指尖血糖数据")
                        else:
                            logger.warning(f"获取指尖血糖数据失败: {response.status}，将从治疗数据中识别")
                            meter_data = []
                except Exception as e:
                    logger.warning(f"获取指尖血糖数据异常: {e}，将从治疗数据中识别")
                    meter_data = []

            # 从治疗数据中识别运动和指尖血糖数据
            filtered_activity_data = []
            filtered_meter_data = []
            
            for item in treatment_data:
                event_type = item.get('eventType', '')
                notes = item.get('notes', '').lower()
                
                # 识别运动数据
                if event_type == 'Exercise' or '运动' in notes or '锻炼' in notes or '跑步' in notes or '乒乓球' in notes or '篮球' in notes or '游泳' in notes:
                    filtered_activity_data.append({
                        'shanghai_time': self.utc_to_shanghai_time(item.get('created_at', '')),  # 修复：转换时区和格式
                        'created_at': item.get('created_at', ''),  # 保留原始时间戳
                        'eventType': event_type or '运动',
                        'duration': item.get('duration', 0),
                        'notes': item.get('notes', '')
                    })
                
                # 识别指尖血糖数据（BG Check事件中的glucose值已经是mmol/L单位）
                if event_type == 'BG Check':
                    glucose_value = item.get('glucose') or item.get('sgv') or item.get('mbg')
                    # 确保数值是合理的mmol/L范围
                    if glucose_value:
                        try:
                            glucose_value = float(glucose_value)
                            if glucose_value > 0:
                                created_at = item.get('created_at', '')
                                filtered_meter_data.append({
                                    'dateString': created_at,  # 添加dateString字段以保持一致性
                                    'shanghai_time': self.utc_to_shanghai_time(created_at),  # 修复：转换时区和格式
                                    'created_at': created_at,  # 保留原始时间戳
                                    'sgv': glucose_value,
                                    'glucose': glucose_value  # 保留原始字段名以确保兼容性
                                })
                                logger.debug(f"从治疗数据识别指尖血糖: 时间={created_at}, 血糖值={glucose_value}")
                        except (ValueError, TypeError):
                            logger.warning(f"指尖血糖数据数值格式错误: {glucose_value}")

            # 如果专用端点没有数据，使用过滤后的数据
            if not activity_data and filtered_activity_data:
                activity_data = filtered_activity_data
                logger.info(f"从治疗数据中识别到 {len(activity_data)} 条运动数据")
            
            if not meter_data and filtered_meter_data:
                meter_data = filtered_meter_data
                logger.info(f"从治疗数据中识别到 {len(meter_data)} 条指尖血糖数据")

            return glucose_data, treatment_data, activity_data, meter_data

        except Exception as e:
            logger.error(f"获取Nightscout数据失败: {e}")
            return [], [], [], []

    def scheduled_analysis(self):
        """定时分析任务"""
        try:
            asyncio.run(self.perform_analysis_and_notify())
        except Exception as e:
            logger.error(f"定时分析失败: {e}")

    def scheduled_sync(self):
        """定时同步任务"""
        try:
            asyncio.run(self.sync_recent_data())
        except Exception as e:
            logger.error(f"定时同步失败: {e}")

    async def perform_analysis_and_notify(self):
        """执行分析并发送通知"""
        # 获取当前时间，格式化为 YYYY-MM-DD HH:MM:SS
        current_time = self._now_in_config_timezone()
        today = current_time.strftime('%Y-%m-%d')
        current_time_str = current_time.strftime('%Y-%m-%d %H:%M:%S')
        
        logger.info(f"开始执行分析，时间范围：{today} 00:00:00 到 {current_time_str}")
        
        # 使用与首页相同的数据获取方式 - 从本地数据库获取当天数据
        today_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = current_time
        
        # 获取当天数据（与首页API逻辑一致）
        glucose_data = self.get_glucose_data_from_db(start_date=today, end_date=today)
        treatment_data = self.get_treatment_data_from_db(start_date=today, end_date=today)
        activity_data = self.get_activity_data_from_db(start_date=today, end_date=today)
        meter_data = self.get_meter_data_from_db(start_date=today, end_date=today)
        
        # 过滤数据，只保留从00:00到当前时间的数据 - 使用统一时间解析
        def filter_data_by_time(data_list):
            """使用统一时间解析函数过滤数据"""
            filtered_data = []
            for item in data_list:
                if item.get('shanghai_time'):
                    try:
                        time_dt = self.parse_time_string(item['shanghai_time'])
                        if today_start <= time_dt <= today_end:
                            filtered_data.append(item)
                    except (ValueError, TypeError):
                        logger.warning(f"数据时间解析失败: {item.get('shanghai_time')}")
                        continue
            return filtered_data
        
        glucose_data = filter_data_by_time(glucose_data)
        treatment_data = filter_data_by_time(treatment_data)
        activity_data = filter_data_by_time(activity_data)
        meter_data = filter_data_by_time(meter_data)
        
        logger.info(f"过滤后数据条数 - 血糖: {len(glucose_data) if glucose_data else 0}, "
                   f"治疗: {len(treatment_data) if treatment_data else 0}, "
                   f"活动: {len(activity_data) if activity_data else 0}, "
                   f"血糖仪: {len(meter_data) if meter_data else 0}")
        
        # 数据已经从本地数据库获取，无需再次保存

        if glucose_data:
            # 使用基于时间的分析（默认启用时间窗口分析）
            analysis = await self.get_ai_analysis(glucose_data, treatment_data, activity_data, meter_data, 1, use_time_window=True)
            
            # 保存分析结果到消息表
            self.save_message("analysis", "血糖分析报告", analysis)
            
            # 发送Web推送通知
            if self.config["notification"]["enable_web_push"]:
                self.send_web_notification("血糖分析报告", analysis[:100] + "...")
            
            # 发送邮件通知
            if self.config["notification"]["enable_email"]:
                self.send_email_notification("血糖分析报告", analysis)

    async def sync_recent_data(self):
        """同步最近的数据"""
        try:
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(hours=2)).strftime('%Y-%m-%d')
            
            logger.info(f"开始同步数据，时间范围: {start_date} 到 {end_date}")
            
            glucose_data, treatment_data, activity_data, meter_data = await self.fetch_nightscout_data(start_date, end_date)
            
            logger.info(f"获取到数据 - 血糖: {len(glucose_data)}, 治疗: {len(treatment_data)}, 运动: {len(activity_data)}, 指尖血糖: {len(meter_data)}")
            
            if glucose_data:
                logger.info(f"保存 {len(glucose_data)} 条血糖数据")
                await self.save_glucose_data(glucose_data)
            if treatment_data:
                logger.info(f"保存 {len(treatment_data)} 条治疗数据")
                await self.save_treatment_data(treatment_data)
            if activity_data:
                logger.info(f"保存 {len(activity_data)} 条运动数据")
                await self.save_activity_data(activity_data)
            if meter_data:
                logger.info(f"保存 {len(meter_data)} 条指尖血糖数据")
                await self.save_meter_data(meter_data)
                
            logger.info("数据同步完成")
            
        except Exception as e:
            logger.error(f"数据同步失败: {e}")
            import traceback
            traceback.print_exc()

    async def save_glucose_data(self, glucose_data: List[Dict]):
        """保存血糖数据到数据库"""
        try:
            conn = sqlite3.connect(self.get_database_path())
            cursor = conn.cursor()

            saved_count = 0
            for entry in glucose_data:
                try:
                    utc_time = entry.get("dateString", "")
                    shanghai_time = self.utc_to_shanghai_time(utc_time)

                    cursor.execute("""
                        INSERT OR IGNORE INTO glucose_data
                        (date_string, shanghai_time, sgv, direction, trend)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        entry.get("dateString"),
                        shanghai_time,
                        entry.get("sgv"),
                        entry.get("direction"),
                        entry.get("trend")
                    ))

                    if cursor.rowcount > 0:
                        saved_count += 1

                except Exception as e:
                    logger.error(f"保存血糖数据项失败: {e}")

            conn.commit()
            # 连接会自动返回到连接池
            logger.info(f"保存了 {saved_count} 条新的血糖数据")
            
            # 清除相关缓存
            if saved_count > 0:
                self._clear_cache("glucose_data")

        except Exception as e:
            logger.error(f"保存血糖数据失败: {e}")

    async def save_treatment_data(self, treatment_data: List[Dict]):
        """保存治疗数据到数据库"""
        try:
            conn = sqlite3.connect(self.get_database_path())
            cursor = conn.cursor()

            saved_count = 0
            for entry in treatment_data:
                try:
                    utc_time = entry.get("created_at") or entry.get("timestamp") or ""
                    shanghai_time = self.utc_to_shanghai_time(utc_time)

                    protein_value = 0
                    fat_value = 0

                    if entry.get("protein"):
                        try:
                            protein_value = float(entry.get("protein"))
                        except (ValueError, TypeError):
                            protein_value = 0

                    if entry.get("fat"):
                        try:
                            fat_value = float(entry.get("fat"))
                        except (ValueError, TypeError):
                            fat_value = 0

                    cursor.execute("""
                        INSERT OR IGNORE INTO treatment_data
                        (date_string, shanghai_time, event_type, carbs, protein, fat, insulin, notes, duration)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        utc_time,
                        shanghai_time,
                        entry.get("eventType", ""),
                        entry.get("carbs", 0),
                        protein_value,
                        fat_value,
                        entry.get("insulin", 0),
                        entry.get("notes", ""),
                        entry.get("duration", 0)
                    ))

                    if cursor.rowcount > 0:
                        saved_count += 1

                except Exception as e:
                    logger.error(f"保存治疗数据项失败: {e}")

            conn.commit()
            conn.close()
            logger.info(f"保存了 {saved_count} 条新的治疗数据")

        except Exception as e:
            logger.error(f"保存治疗数据失败: {e}")

    async def save_activity_data(self, activity_data: List[Dict]):
        """保存运动数据到数据库"""
        try:
            conn = sqlite3.connect(self.get_database_path())
            cursor = conn.cursor()

            saved_count = 0
            for entry in activity_data:
                try:
                    # 支持多种可能的字段名获取UTC时间
                    utc_time = entry.get("created_at") or entry.get("timestamp") or entry.get("dateString") or ""
                    if not utc_time and entry.get("shanghai_time"):
                        # 如果只有shanghai_time，尝试反向转换为UTC时间
                        try:
                            dt = datetime.strptime(entry["shanghai_time"], '%Y-%m-%d %H:%M:%S')
                            # 假设shanghai_time是UTC+8，转换为UTC时间
                            utc_dt = dt - timedelta(hours=8)
                            utc_time = utc_dt.isoformat() + 'Z'
                        except (ValueError, TypeError):
                            logger.warning(f"无法从shanghai_time转换utc_time: {entry.get('shanghai_time')}")
                            utc_time = ""
                    shanghai_time = self.utc_to_shanghai_time(utc_time)

                    cursor.execute("""
                        INSERT OR IGNORE INTO activity_data
                        (date_string, shanghai_time, event_type, duration, notes)
                        VALUES (?, ?, ?, ?, ?)
                    """, (
                        utc_time,
                        shanghai_time,
                        entry.get("eventType", ""),
                        entry.get("duration", 0),
                        entry.get("notes", "")
                    ))

                    if cursor.rowcount > 0:
                        saved_count += 1

                except Exception as e:
                    logger.error(f"保存运动数据项失败: {e}")

            conn.commit()
            conn.close()
            logger.info(f"保存了 {saved_count} 条新的运动数据")

        except Exception as e:
            logger.error(f"保存运动数据失败: {e}")

    async def save_meter_data(self, meter_data: List[Dict]):
        """保存指尖血糖数据到数据库"""
        try:
            conn = sqlite3.connect(self.get_database_path())
            cursor = conn.cursor()

            saved_count = 0
            for entry in meter_data:
                try:
                    # 支持多种可能的字段名获取UTC时间
                    utc_time = entry.get("dateString") or entry.get("created_at") or entry.get("timestamp") or ""
                    if not utc_time and entry.get("shanghai_time"):
                        # 如果只有shanghai_time，尝试反向转换为UTC时间
                        try:
                            dt = datetime.strptime(entry["shanghai_time"], '%Y-%m-%d %H:%M:%S')
                            # 假设shanghai_time是UTC+8，转换为UTC时间
                            utc_dt = dt - timedelta(hours=8)
                            utc_time = utc_dt.isoformat() + 'Z'
                        except (ValueError, TypeError):
                            logger.warning(f"无法从shanghai_time转换utc_time: {entry.get('shanghai_time')}")
                            utc_time = ""
                    shanghai_time = self.utc_to_shanghai_time(utc_time)
                    
                    # 获取血糖值 - 支持多种字段名
                    sgv_value = entry.get("sgv") or entry.get("glucose") or entry.get("mbg")
                    if sgv_value is None:
                        logger.warning(f"指尖血糖数据缺少sgv/glucose字段: {entry}")
                        continue

                    # 确保血糖值是数值类型
                    try:
                        sgv_value = float(sgv_value)
                        if sgv_value <= 0:
                            logger.warning(f"指尖血糖数据数值无效: {sgv_value}")
                            continue
                    except (ValueError, TypeError):
                        logger.warning(f"指尖血糖数据数值格式错误: {sgv_value}")
                        continue

                    # 确保有date_string用于唯一性约束
                    date_string = entry.get("dateString") or utc_time or shanghai_time
                    if not date_string:
                        logger.warning(f"指尖血糖数据缺少有效的时间戳: {entry}")
                        continue

                    cursor.execute("""
                        INSERT OR IGNORE INTO meter_data
                        (date_string, shanghai_time, sgv)
                        VALUES (?, ?, ?)
                    """, (
                        date_string,
                        shanghai_time,
                        sgv_value
                    ))

                    if cursor.rowcount > 0:
                        saved_count += 1
                        logger.debug(f"成功保存指尖血糖数据: {date_string}, {shanghai_time}, {sgv_value}")
                    else:
                        logger.debug(f"指尖血糖数据已存在，跳过: {date_string}")

                except Exception as e:
                    logger.error(f"保存指尖血糖数据项失败: {e}, 数据项: {entry}")

            conn.commit()
            conn.close()
            logger.info(f"保存了 {saved_count} 条新的指尖血糖数据，共处理 {len(meter_data)} 条数据")

        except Exception as e:
            logger.error(f"保存指尖血糖数据失败: {e}")

    def get_glucose_data_from_db(self, days: int = 7, start_date: Optional[str] = None, end_date: Optional[str] = None, limit: Optional[int] = None, offset: int = 0, use_cache: bool = True) -> List[Dict]:
        """从数据库获取血糖数据 - 性能优化版本"""
        # 生成缓存键
        cache_key = self._get_cache_key("glucose_data", days, start_date, end_date, limit, offset)
        
        # 尝试从缓存获取数据
        if use_cache:
            cached_data = self._get_cache(cache_key)
            if cached_data is not None:
                return cached_data
        
        try:
            with self.get_db_connection() as conn:
                cursor = conn.cursor()

                if start_date and end_date:
                    # 确保结束日期包含全天
                    end_date_dt = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
                    end_date_str = end_date_dt.strftime('%Y-%m-%d')
                    query = """
                        SELECT date_string, shanghai_time, sgv, direction, trend
                        FROM glucose_data
                        WHERE shanghai_time >= ? AND shanghai_time < ?
                        ORDER BY date_string DESC
                    """
                    params = (start_date, end_date_str)
                else:
                    start_date_str = (self._now_in_config_timezone() - timedelta(days=days-1)).strftime('%Y-%m-%d')
                    query = """
                        SELECT date_string, shanghai_time, sgv, direction, trend
                        FROM glucose_data
                        WHERE shanghai_time >= ?
                        ORDER BY date_string DESC
                    """
                    params = (start_date_str,)
                    
                # 添加分页支持
                if limit is not None:
                    query += " LIMIT ? OFFSET ?"
                    params = params + (limit, offset)

                cursor.execute(query, params)
                rows = cursor.fetchall()
                # 连接会自动返回到连接池

            glucose_data = []
            for row in rows:
                glucose_data.append({
                    "dateString": row[0],
                    "shanghai_time": row[1],
                    "sgv": row[2],
                    "direction": row[3],
                    "trend": row[4]
                })
            
            # 缓存结果（5分钟TTL）
            if use_cache and glucose_data:
                self._set_cache(cache_key, glucose_data, ttl_seconds=300)

            return glucose_data

        except Exception as e:
            logger.error(f"从数据库获取血糖数据失败: {e}")
            return []

    def get_treatment_data_from_db(self, days: int = 7, start_date: Optional[str] = None, end_date: Optional[str] = None) -> List[Dict]:
        """从数据库获取治疗数据"""
        try:
            conn = sqlite3.connect(self.get_database_path())
            cursor = conn.cursor()

            if start_date and end_date:
                # 确保结束日期包含全天
                end_date_dt = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
                end_date_str = end_date_dt.strftime('%Y-%m-%d')
                query = """
                    SELECT date_string, shanghai_time, event_type, carbs, protein, fat, insulin, notes, duration
                    FROM treatment_data
                    WHERE shanghai_time >= ? AND shanghai_time < ?
                    ORDER BY date_string DESC
                """
                params = (start_date, end_date_str)
            else:
                start_date_str = (self._now_in_config_timezone() - timedelta(days=days-1)).strftime('%Y-%m-%d')
                query = """
                    SELECT date_string, shanghai_time, event_type, carbs, protein, fat, insulin, notes, duration
                    FROM treatment_data
                    WHERE shanghai_time >= ?
                    ORDER BY date_string DESC
                """
                params = (start_date_str,)

            cursor.execute(query, params)
            rows = cursor.fetchall()
            conn.close()

            treatment_data = []
            for row in rows:
                treatment_data.append({
                    "created_at": row[0],
                    "shanghai_time": row[1],
                    "eventType": row[2],
                    "carbs": row[3],
                    "protein": row[4],
                    "fat": row[5],
                    "insulin": row[6],
                    "notes": row[7],
                    "duration": row[8]
                })

            return treatment_data

        except Exception as e:
            logger.error(f"从数据库获取治疗数据失败: {e}")
            return []

    def get_activity_data_from_db(self, days: int = 7, start_date: Optional[str] = None, end_date: Optional[str] = None) -> List[Dict]:
        """从数据库获取运动数据"""
        try:
            conn = sqlite3.connect(self.get_database_path())
            cursor = conn.cursor()

            if start_date and end_date:
                end_date_dt = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
                end_date_str = end_date_dt.strftime('%Y-%m-%d')
                query = """
                    SELECT date_string, shanghai_time, event_type, duration, notes
                    FROM activity_data
                    WHERE shanghai_time >= ? AND shanghai_time < ?
                    ORDER BY date_string DESC
                """
                params = (start_date, end_date_str)
            else:
                start_date_str = (self._now_in_config_timezone() - timedelta(days=days-1)).strftime('%Y-%m-%d')
                query = """
                    SELECT date_string, shanghai_time, event_type, duration, notes
                    FROM activity_data
                    WHERE shanghai_time >= ?
                    ORDER BY date_string DESC
                """
                params = (start_date_str,)

            cursor.execute(query, params)
            rows = cursor.fetchall()
            conn.close()

            activity_data = []
            for row in rows:
                activity_data.append({
                    "created_at": row[0],
                    "shanghai_time": row[1],
                    "eventType": row[2],
                    "duration": row[3],
                    "notes": row[4]
                })

            return activity_data

        except Exception as e:
            logger.error(f"从数据库获取运动数据失败: {e}")
            return []

    def get_meter_data_from_db(self, days: int = 7, start_date: Optional[str] = None, end_date: Optional[str] = None) -> List[Dict]:
        """从数据库获取指尖血糖数据"""
        try:
            conn = sqlite3.connect(self.get_database_path())
            cursor = conn.cursor()

            if start_date and end_date:
                end_date_dt = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
                end_date_str = end_date_dt.strftime('%Y-%m-%d')
                query = """
                    SELECT date_string, shanghai_time, sgv
                    FROM meter_data
                    WHERE shanghai_time >= ? AND shanghai_time < ?
                    ORDER BY date_string DESC
                """
                params = (start_date, end_date_str)
            else:
                start_date_str = (self._now_in_config_timezone() - timedelta(days=days-1)).strftime('%Y-%m-%d')
                query = """
                    SELECT date_string, shanghai_time, sgv
                    FROM meter_data
                    WHERE shanghai_time >= ?
                    ORDER BY date_string DESC
                """
                params = (start_date_str,)

            cursor.execute(query, params)
            rows = cursor.fetchall()
            conn.close()

            meter_data = []
            for row in rows:
                meter_data.append({
                    "date_string": row[0],
                    "shanghai_time": row[1],
                    "sgv": row[2]
                })

            return meter_data

        except Exception as e:
            logger.error(f"从数据库获取指尖血糖数据失败: {e}")
            return []

    def save_message(self, message_type: str, title: str, content: str) -> bool:
        """保存消息到数据库"""
        try:
            conn = sqlite3.connect(self.get_database_path())
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO messages (type, title, content)
                VALUES (?, ?, ?)
            """, (message_type, title, content))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"保存消息失败: {e}")
            return False
    
    # 时间窗口常量定义
    class TimeWindows:
        """时间窗口常量定义"""
        FASTING_START = 5
        FASTING_END = 7
        FASTING_TARGET = 6
        
        MEAL_WINDOWS = {
            'breakfast': {'meal_start': 6, 'meal_end': 9, 'fallback_start': 8, 'fallback_end': 10},
            'lunch': {'meal_start': 11, 'meal_end': 14, 'fallback_start': 12, 'fallback_end': 14},
            'dinner': {'meal_start': 17, 'meal_end': 19, 'fallback_start': 18, 'fallback_end': 20}
        }
        
        SEARCH_WINDOW_MINUTES = 45  # 增加搜索窗口到45分钟，更好适应5分钟间隔的数据
    
    def _get_valid_timezone_offset(self) -> int:
        """获取并验证时区偏移值
        
        Returns:
            有效的时区偏移值，默认为8 (UTC+8)
        """
        try:
            timezone_offset = self.config.get("basic", {}).get("timezone_offset", 8)
            if isinstance(timezone_offset, (int, float)) and -12 <= timezone_offset <= 14:
                return int(timezone_offset)
            else:
                logger.warning(f"无效的时区偏移配置: {timezone_offset}，使用默认值UTC+8")
                return 8
        except Exception as e:
            logger.error(f"读取时区配置失败: {e}，使用默认值UTC+8")
            return 8

    def _format_timezone_name(self, timezone_offset: int) -> str:
        """格式化时区名称（例如UTC+8/UTC-5）"""
        sign = "+" if timezone_offset >= 0 else ""
        return f"UTC{sign}{timezone_offset}"

    def _now_in_config_timezone(self) -> datetime:
        """获取配置时区的当前本地时间（无时区信息，和shanghai_time字段保持一致）"""
        timezone_offset = self._get_valid_timezone_offset()
        target_tz = timezone(timedelta(hours=timezone_offset))
        return datetime.now(timezone.utc).astimezone(target_tz).replace(tzinfo=None)
    
    def _convert_to_local_hour(self, timestamp: datetime, timezone_offset: int) -> Optional[int]:
        """将UTC时间戳转换为本地时间小时
        
        Args:
            timestamp: UTC时间戳
            timezone_offset: 时区偏移
            
        Returns:
            本地时间小时数，如果转换失败返回None
        """
        try:
            if not timestamp:
                return None
            local_hour = (timestamp.hour + timezone_offset) % 24
            return local_hour
        except Exception as e:
            logger.error(f"时区转换失败: {e}")
            return None
    
    def _validate_glucose_data(self, glucose_data: List[Dict]) -> bool:
        """验证血糖数据的基本完整性
        
        Args:
            glucose_data: 血糖数据列表
            
        Returns:
            数据是否有效
        """
        if not glucose_data or not isinstance(glucose_data, list):
            return False
        return all(isinstance(item, dict) and 'value' in item for item in glucose_data)
    
    def _sanitize_glucose_value(self, value: Any) -> Optional[float]:
        """清理和验证血糖值
        
        Args:
            value: 原始血糖值
            
        Returns:
            清理后的血糖值，如果无效返回None
        """
        try:
            if value is None:
                return None
            float_value = float(value)
            if 0 <= float_value <= 50:  # 合理的血糖值范围
                return float_value
            else:
                logger.warning(f"血糖值超出合理范围: {float_value}")
                return None
        except (ValueError, TypeError):
            logger.warning(f"无效的血糖值: {value}")
            return None
    
    def _filter_glucose_by_date(self, glucose_data: List[Dict], target_date: datetime, timezone_offset: int) -> List[Dict]:
        """按日期筛选血糖数据并转换时区
        
        Args:
            glucose_data: 血糖数据列表
            target_date: 目标日期
            timezone_offset: 时区偏移
            
        Returns:
            筛选后的血糖数据列表
        """
        target_date_str = target_date.strftime('%Y-%m-%d')
        result = []
        
        for g in glucose_data:
            if 'timestamp' in g and g['timestamp']:
                # 使用timestamp的时间戳进行日期匹配
                glucose_date_str = g['timestamp'].strftime('%Y-%m-%d')
                if glucose_date_str == target_date_str:
                    local_hour = self._convert_to_local_hour(g['timestamp'], timezone_offset)
                    if local_hour is not None:
                        g_copy = g.copy()
                        g_copy['hour'] = local_hour
                        result.append(g_copy)
                    else:
                        result.append(g)
            elif 'date_string' in g and g['date_string'] == target_date_str:
                # 如果有date_string字段，直接使用
                if 'hour' not in g and 'timestamp' in g and g['timestamp']:
                    local_hour = self._convert_to_local_hour(g['timestamp'], timezone_offset)
                    if local_hour is not None:
                        g_copy = g.copy()
                        g_copy['hour'] = local_hour
                        result.append(g_copy)
                    else:
                        result.append(g)
                else:
                    result.append(g)
        
        return result

    def calculate_fasting_glucose(self, glucose_data: List[Dict], target_date: datetime) -> Optional[float]:
        """计算空腹血糖值（早上6:00 AM，UTC+8）
        
        Args:
            glucose_data: 血糖数据列表
            target_date: 目标日期
            
        Returns:
            空腹血糖值，如果没有找到则返回None
        """
        try:
            # 验证输入数据
            if not self._validate_glucose_data(glucose_data):
                logger.warning("血糖数据验证失败")
                return None
            
            # 获取有效的时区偏移
            timezone_offset = self._get_valid_timezone_offset()
            
            # 筛选目标日期的血糖数据
            day_glucose = self._filter_glucose_by_date(glucose_data, target_date, timezone_offset)
            
            # 计算空腹血糖：早上6:00 AM，搜索窗口5:00-7:00 AM
            fasting_glucose = None
            
            # 优先精确匹配6:00 AM
            exact_match = next((g['value'] for g in day_glucose if 'hour' in g and g['hour'] == self.TimeWindows.FASTING_TARGET), None)
            if exact_match:
                fasting_glucose = self._sanitize_glucose_value(exact_match)
            else:
                # 如果没有精确匹配，搜索5:00-7:00 AM窗口
                window_matches = [
                    g['value'] for g in day_glucose 
                    if 'hour' in g and self.TimeWindows.FASTING_START <= g['hour'] <= self.TimeWindows.FASTING_END
                ]
                if window_matches:
                    # 选择最接近6:00 AM的值
                    window_matches_sanitized = [self._sanitize_glucose_value(v) for v in window_matches if self._sanitize_glucose_value(v) is not None]
                    if window_matches_sanitized:
                        fasting_glucose = window_matches_sanitized[0]  # 取第一个有效值
            
            logger.info(f"计算空腹血糖 - 日期: {target_date.strftime('%Y-%m-%d')}, 结果: {fasting_glucose}")
            return fasting_glucose
            
        except Exception as e:
            logger.error(f"计算空腹血糖失败: {e}")
            return None
    
    def calculate_postprandial_glucose(self, glucose_data: List[Dict], meals_data: List[Dict], target_date: datetime, meal_type: str) -> Optional[float]:
        """计算餐后2小时血糖值
        
        Args:
            glucose_data: 血糖数据列表
            meals_data: 餐食数据列表
            target_date: 目标日期
            meal_type: 餐食类型 ('breakfast', 'lunch', 'dinner')
            
        Returns:
            餐后2小时血糖值，如果没有找到则返回None
        """
        try:
            # 验证输入数据
            if not self._validate_glucose_data(glucose_data):
                logger.warning("血糖数据验证失败")
                return None
            
            # 获取有效的时区偏移
            timezone_offset = self._get_valid_timezone_offset()
            
            # 检查餐食类型是否支持
            if meal_type not in self.TimeWindows.MEAL_WINDOWS:
                logger.error(f"不支持的餐食类型: {meal_type}")
                return None
            
            time_window = self.TimeWindows.MEAL_WINDOWS[meal_type]
            
            # 筛选目标日期的血糖数据
            day_glucose = self._filter_glucose_by_date(glucose_data, target_date, timezone_offset)
            
            # 筛选目标日期的餐食数据
            day_meals = []
            target_date_str = target_date.strftime('%Y-%m-%d')
            for meal in meals_data:
                if 'timestamp' in meal and meal['timestamp']:
                    meal_date_str = meal['timestamp'].strftime('%Y-%m-%d')
                    if meal_date_str == target_date_str:
                        local_hour = self._convert_to_local_hour(meal['timestamp'], timezone_offset)
                        if local_hour is not None:
                            meal_copy = meal.copy()
                            meal_copy['hour'] = local_hour
                            day_meals.append(meal_copy)
                        else:
                            day_meals.append(meal)
                elif 'date_string' in meal and meal['date_string'] == target_date_str:
                    if 'hour' not in meal and 'timestamp' in meal and meal['timestamp']:
                        local_hour = self._convert_to_local_hour(meal['timestamp'], timezone_offset)
                        if local_hour is not None:
                            meal_copy = meal.copy()
                            meal_copy['hour'] = local_hour
                            day_meals.append(meal_copy)
                        else:
                            day_meals.append(meal)
                    else:
                        day_meals.append(meal)
            
            postprandial_glucose = None
            
            # 基于实际餐食时间计算餐后2小时血糖
            if day_meals:
                # 查找指定类型的餐食记录
                meal_records = [m for m in day_meals if 'hour' in m and 
                               time_window['meal_start'] <= m['hour'] < time_window['meal_end']]
                
                logger.debug(f"早餐数据计算 - 日期: {target_date.strftime('%Y-%m-%d')}, 餐食类型: {meal_type}")
                logger.debug(f"时间窗口: {time_window}")
                logger.debug(f"当日餐食记录数: {len(day_meals)}")
                logger.debug(f"符合时间窗口的餐食记录数: {len(meal_records)}")
                
                if meal_records:
                    # 取最早的餐食记录
                    meal_record = min(meal_records, key=lambda x: x['hour'])
                    logger.debug(f"选择的餐食记录: {meal_record}")
                    
                    if 'timestamp' in meal_record and meal_record['timestamp']:
                        # 计算餐后2小时的目标时间
                        target_time = meal_record['timestamp'] + timedelta(hours=2)
                        
                        # 获取目标时间前后30分钟内的所有血糖数据
                        time_window_start = target_time - timedelta(minutes=self.TimeWindows.SEARCH_WINDOW_MINUTES)
                        time_window_end = target_time + timedelta(minutes=self.TimeWindows.SEARCH_WINDOW_MINUTES)
                        
                        # 从中选择最接近目标时间的血糖值
                        window_glucose = [
                            g for g in day_glucose
                            if g['timestamp'] and
                               time_window_start <= g['timestamp'] <= time_window_end
                        ]
                        
                        if window_glucose:
                            # 找到最接近目标时间的血糖值
                            closest_glucose = min(
                                window_glucose,
                                key=lambda x: abs(x['timestamp'] - target_time)
                            )
                            time_diff = abs(closest_glucose['timestamp'] - target_time)
                            postprandial_glucose = self._sanitize_glucose_value(closest_glucose['value'])
                            
                            logger.info(f"找到餐后血糖 - 餐食类型: {meal_type}, 目标时间: {target_time.strftime('%H:%M')}, "
                                      f"实际时间: {closest_glucose['timestamp'].strftime('%H:%M')}, "
                                      f"时间差: {time_diff.total_seconds()/60:.1f}分钟, "
                                      f"血糖值: {postprandial_glucose}")
                        else:
                            logger.warning(f"未找到餐后血糖 - 餐食类型: {meal_type}, 目标时间: {target_time.strftime('%H:%M')}, "
                                         f"搜索窗口: {time_window_start.strftime('%H:%M')}-{time_window_end.strftime('%H:%M')}, "
                                         f"可用血糖数据: {len(day_glucose)}条")
            
            # 如果没有找到基于餐食时间的餐后血糖，使用固定时间窗口逻辑
            if postprandial_glucose is None:
                logger.debug(f"使用固定时间窗口逻辑，回退时间范围: {time_window['fallback_start']}:00-{time_window['fallback_end']}:00")
                fallback_glucose = next(
                    (g['value'] for g in day_glucose if 'hour' in g and 
                     time_window['fallback_start'] <= g['hour'] < time_window['fallback_end']), 
                    None
                )
                postprandial_glucose = self._sanitize_glucose_value(fallback_glucose)
                logger.debug(f"回退逻辑找到的血糖值: {fallback_glucose}, 处理后: {postprandial_glucose}")
            else:
                logger.debug(f"基于餐食时间找到的餐后血糖: {postprandial_glucose}")
            
            logger.info(f"计算餐后2小时血糖 - 日期: {target_date.strftime('%Y-%m-%d')}, 餐食类型: {meal_type}, 结果: {postprandial_glucose}")
            return postprandial_glucose
            
        except Exception as e:
            logger.error(f"计算餐后2小时血糖失败: {e}")
            return None

    def convert_to_beijing_time(self, dt_str: str) -> str:
        """将UTC时间字符串转换为配置的时区时间字符串"""
        try:
            if not dt_str:
                return dt_str
            
            # 解析UTC时间字符串
            if 'T' in dt_str and 'Z' in dt_str:
                # ISO格式: 2023-12-07T12:34:56Z
                dt = datetime.fromisoformat(dt_str.replace('Z', '+00:00'))
            elif ' ' in dt_str and '.' in dt_str:
                # 数据库格式: 2023-12-07 12:34:56.123456
                dt = datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S.%f')
            elif ' ' in dt_str:
                # 简单格式: 2023-12-07 12:34:56
                dt = datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S')
            else:
                # 其他格式，尝试直接解析
                dt = datetime.fromisoformat(dt_str)
            
            # 从配置读取时区偏移，默认为UTC+8（北京时间）
            timezone_offset = 8  # 默认值
            try:
                if hasattr(self, 'config') and isinstance(self.config, dict):
                    if "basic" in self.config and isinstance(self.config["basic"], dict):
                        offset_config = self.config["basic"].get("timezone_offset", 8)
                        # 验证时区偏移值的有效性（-12到14之间）
                        if isinstance(offset_config, (int, float)) and -12 <= offset_config <= 14:
                            timezone_offset = offset_config
                        else:
                            logger.warning(f"无效的时区偏移配置: {offset_config}，使用默认值UTC+8")
                    else:
                        logger.warning("配置中缺少basic节，使用默认时区偏移UTC+8")
                else:
                    logger.warning("配置对象无效，使用默认时区偏移UTC+8")
            except Exception as config_error:
                logger.error(f"读取时区配置失败: {config_error}，使用默认值UTC+8")
            
            # 使用配置的时区偏移进行转换
            target_dt = dt + timedelta(hours=timezone_offset)
            
            # 格式化为 YYYY-MM-DD HH:MM:SS
            return target_dt.strftime('%Y-%m-%d %H:%M:%S')
        except Exception as e:
            logger.error(f"时区转换失败: {e}, 原时间字符串: {dt_str}")
            return dt_str

    def get_messages(self, message_type: Optional[str] = None, limit: int = 50) -> List[Dict]:
        """从数据库获取消息"""
        try:
            conn = sqlite3.connect(self.get_database_path())
            cursor = conn.cursor()
            
            if message_type:
                cursor.execute("""
                    SELECT id, type, title, content, is_read, is_favorite, created_at
                    FROM messages
                    WHERE type = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (message_type, limit))
            else:
                cursor.execute("""
                    SELECT id, type, title, content, is_read, is_favorite, created_at
                    FROM messages
                    ORDER BY created_at DESC
                    LIMIT ?
                """, (limit,))
            
            rows = cursor.fetchall()
            conn.close()
            
            messages = []
            for row in rows:
                messages.append({
                    'id': row[0],
                    'type': row[1],
                    'title': row[2],
                    'content': row[3],
                    'is_read': bool(row[4]),
                    'is_favorite': bool(row[5]),
                    'created_at': row[6],
                    'created_at_beijing': self.convert_to_beijing_time(row[6])
                })
            
            return messages
        except Exception as e:
            logger.error(f"获取消息失败: {e}")
            return []
    
    def update_message_status(self, message_id: int, is_read: Optional[bool] = None, is_favorite: Optional[bool] = None) -> bool:
        """更新消息状态"""
        try:
            conn = sqlite3.connect(self.get_database_path())
            cursor = conn.cursor()
            
            if is_read is not None and is_favorite is not None:
                cursor.execute("""
                    UPDATE messages
                    SET is_read = ?, is_favorite = ?
                    WHERE id = ?
                """, (is_read, is_favorite, message_id))
            elif is_read is not None:
                cursor.execute("""
                    UPDATE messages
                    SET is_read = ?
                    WHERE id = ?
                """, (is_read, message_id))
            elif is_favorite is not None:
                cursor.execute("""
                    UPDATE messages
                    SET is_favorite = ?
                    WHERE id = ?
                """, (is_favorite, message_id))
            else:
                return False
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"更新消息状态失败: {e}")
            return False
    
    def delete_message(self, message_id: int) -> bool:
        """删除消息"""
        try:
            conn = sqlite3.connect(self.get_database_path())
            cursor = conn.cursor()
            
            cursor.execute("""
                DELETE FROM messages
                WHERE id = ?
            """, (message_id,))
            
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"删除消息失败: {e}")
            return False

    def delete_messages_batch(self, message_ids: List[int]) -> bool:
        """批量删除消息"""
        try:
            if not message_ids:
                logger.warning("批量删除消息：消息ID列表为空")
                return False
            
            conn = sqlite3.connect(self.get_database_path())
            cursor = conn.cursor()
            
            # 使用IN子句批量删除
            placeholders = ','.join('?' for _ in message_ids)
            cursor.execute(f"""
                DELETE FROM messages
                WHERE id IN ({placeholders})
            """, message_ids)
            
            deleted_count = cursor.rowcount
            conn.commit()
            conn.close()
            
            logger.info(f"批量删除消息：成功删除 {deleted_count} 条消息")
            return True
        except Exception as e:
            logger.error(f"批量删除消息失败: {e}")
            return False
    
    def get_unread_message_count(self) -> int:
        """获取未读消息数量"""
        try:
            conn = sqlite3.connect(self.get_database_path())
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT COUNT(*)
                FROM messages
                WHERE is_read = 0
            """)
            
            count = cursor.fetchone()[0]
            conn.close()
            return count
        except Exception as e:
            logger.error(f"获取未读消息数量失败: {e}")
            return 0

    @ai_retry_decorator(max_retries=3)
    async def _make_ai_analysis_request(self, prompt: str) -> str:
        """执行AI分析HTTP请求（带有重试机制）"""
        request_data = {
            "model": self.config["ai_config"]["model_name"],
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.7,
            "top_p": 0.9,
            "max_tokens": 16000,
            "stream": False
        }

        headers = {
            "Content-Type": "application/json"
        }

        if self.config["ai_config"]["api_key"]:
            headers["Authorization"] = f"Bearer {self.config['ai_config']['api_key']}"

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.config["ai_config"]["timeout"])) as session:
            async with session.post(self.config["ai_config"]["api_url"], json=request_data, headers=headers) as response:
                if response.status == 200:
                    result = await response.json()
                    if 'choices' in result and len(result['choices']) > 0:
                        ai_analysis = result['choices'][0]['message']['content'].strip()
                        return ai_analysis
                    else:
                        raise ValueError(f"AI响应格式错误: {result}")
                else:
                    error_text = await response.text()
                    raise Exception(f"AI请求HTTP错误: {response.status} - {error_text}")

    async def get_ai_analysis(self, glucose_data: List[Dict], treatment_data: List[Dict], activity_data: List[Dict], meter_data: List[Dict], days: int = 1, use_time_window: bool = True, use_smart_range: bool = True) -> str:
        """获取AI分析结果
        
        Args:
            glucose_data: 血糖数据
            treatment_data: 治疗数据
            activity_data: 活动数据
            meter_data: 指尖血糖数据
            days: 分析天数
            use_time_window: 是否使用时间窗口分析，默认为True（保持向后兼容）
            use_smart_range: 是否使用智能数据范围分析，默认为True
        """
        try:
            # 数据验证 - 在进行任何处理之前先验证数据质量
            validation_result = self.validate_glucose_data(glucose_data, treatment_data, activity_data, meter_data)
            
            # 如果数据验证失败，返回相应的错误信息
            if not validation_result["is_valid"]:
                if validation_result["data_quality_score"] == 0:
                    return "没有可用的血糖数据进行分析，请先同步数据。"
                else:
                    warnings_summary = "；".join(validation_result["warnings"][:3])  # 限制显示前3个警告
                    return f"数据质量较差，无法进行准确分析：{warnings_summary}。建议补充更多数据后再试。"
            
            # 记录数据质量信息
            if validation_result["data_quality_score"] < 80:
                logger.warning(f"数据质量分数较低: {validation_result['data_quality_score']} - 将继续分析但结果可能不够准确")
            
            # 动态数据范围分析初始化 - 使用配置的本地时区（和shanghai_time保持一致）
            timezone_offset = self._get_valid_timezone_offset()
            timezone_name = self._format_timezone_name(timezone_offset)
            current_time = self._now_in_config_timezone()
            
            # 禁用时间窗口分段逻辑，始终使用动态数据范围
            time_window = None
            use_time_window = False
            use_smart_range = True
            
            data_completeness = None
            filtered_glucose_data = glucose_data
            filtered_activity_data = activity_data
            filtered_meter_data = meter_data
            
            # 获取动态数据范围（00:00到当前分析时间）
            try:
                # 获取动态数据范围
                dynamic_range = self.get_dynamic_data_range(current_time)
                logger.info(f"动态数据范围: {dynamic_range['range_description']} ({timezone_name}时间: {current_time.strftime('%Y-%m-%d %H:%M:%S')})")
                
                # 暂时禁用动态数据范围过滤，使用全量数据
                # filtered_glucose_data = self.filter_data_by_dynamic_range(glucose_data, dynamic_range, "glucose")
                # filtered_activity_data = self.filter_data_by_dynamic_range(activity_data, dynamic_range, "activity")
                # filtered_meter_data = self.filter_data_by_dynamic_range(meter_data, dynamic_range, "meter")
                filtered_glucose_data = glucose_data
                filtered_activity_data = activity_data
                filtered_meter_data = meter_data
                
                # 暂时禁用过滤，所有数据都使用全量数据
                logger.info(f"数据过滤已禁用 - 血糖: {len(filtered_glucose_data)}, 活动: {len(filtered_activity_data)}, 指尖血糖: {len(filtered_meter_data)}, 治疗: {len(treatment_data)}")
                
                # 检查数据完整性
                data_completeness = self.check_data_completeness(
                    filtered_glucose_data, treatment_data, filtered_activity_data, 
                    filtered_meter_data, dynamic_range
                )
                logger.info(f"数据完整性检查完成 - 评分: {data_completeness['completeness_score']}, 完整: {data_completeness['is_complete']}")
                
                # 如果数据完整性过低，记录警告但仍继续分析
                if data_completeness['completeness_score'] < 70:
                    logger.warning(f"数据完整性评分{data_completeness['completeness_score']}低于阈值70，分析结果可能不够准确")
                
            except Exception as e:
                logger.error(f"动态数据范围分析失败: {e}，将使用全量数据进行分析")
                # 如果动态数据范围分析失败，使用全量数据
                filtered_glucose_data = glucose_data
                filtered_activity_data = activity_data
                filtered_meter_data = meter_data
                data_completeness = None
            
            # 验证过滤后的数据
            if not filtered_glucose_data:
                logger.warning("数据过滤后没有血糖数据，将使用全量数据进行分析")
                filtered_glucose_data = glucose_data
                filtered_activity_data = activity_data
                filtered_meter_data = meter_data
                time_window = None
                data_completeness = None
            
            # 计算关键血糖值：空腹血糖和餐后血糖
            key_glucose_values = {}
            
            try:
                # 获取最近几天的日期用于计算关键血糖值
                analysis_dates = []
                for i in range(days):
                    date = current_time - timedelta(days=i)
                    analysis_dates.append(date)
                
                # 计算空腹血糖（6:00 AM）
                fasting_values = []
                for date in analysis_dates:
                    fasting_value = self.calculate_fasting_glucose(glucose_data, date)
                    if fasting_value is not None:
                        fasting_values.append(fasting_value)
                
                if fasting_values:
                    key_glucose_values['fasting'] = {
                        'values': fasting_values,
                        'average': sum(fasting_values) / len(fasting_values),
                        'latest': fasting_values[0] if fasting_values else None,
                        'count': len(fasting_values)
                    }
                    logger.info(f"计算空腹血糖完成 - 数量: {len(fasting_values)}, 平均值: {key_glucose_values['fasting']['average']:.1f}")
                
                # 计算餐后2小时血糖
                postprandial_values = {'breakfast': [], 'lunch': [], 'dinner': []}
                
                for meal_type in ['breakfast', 'lunch', 'dinner']:
                    meal_values = []
                    logger.debug(f"开始计算{meal_type}餐后血糖，分析日期数: {len(analysis_dates)}")
                    for date in analysis_dates:
                        postprandial_value = self.calculate_postprandial_glucose(
                            glucose_data, treatment_data, date, meal_type
                        )
                        if postprandial_value is not None:
                            meal_values.append(postprandial_value)
                            logger.debug(f"{meal_type} - {date.strftime('%Y-%m-%d')}: 找到餐后血糖 {postprandial_value}")
                        else:
                            logger.debug(f"{meal_type} - {date.strftime('%Y-%m-%d')}: 未找到餐后血糖")
                    
                    if meal_values:
                        postprandial_values[meal_type] = {
                            'values': meal_values,
                            'average': sum(meal_values) / len(meal_values),
                            'latest': meal_values[0] if meal_values else None,
                            'count': len(meal_values)
                        }
                        logger.info(f"计算餐后2小时血糖({meal_type})完成 - 数量: {len(meal_values)}, 平均值: {postprandial_values[meal_type]['average']:.1f}")
                
                # 如果有餐后血糖数据，添加到关键血糖值
                if any(postprandial_values[meal_type] for meal_type in ['breakfast', 'lunch', 'dinner']):
                    key_glucose_values['postprandial'] = postprandial_values
                
                logger.info(f"关键血糖值计算完成 - 空腹: {'有' if key_glucose_values.get('fasting') else '无'}, 餐后: {'有' if key_glucose_values.get('postprandial') else '无'}")
                
                # 详细记录餐后血糖数据
                if key_glucose_values.get('postprandial'):
                    postprandial_data = key_glucose_values['postprandial']
                    for meal_type, meal_info in postprandial_data.items():
                        if meal_info and isinstance(meal_info, dict) and meal_info.get('values'):
                            logger.info(f"{meal_type}餐后血糖数据: 平均{meal_info['average']:.1f}, 最新{meal_info['latest']:.1f}, 测量次数{meal_info['count']}")
                        else:
                            logger.info(f"{meal_type}餐后血糖数据: 无数据")
                else:
                    logger.info("餐后血糖数据: 无任何餐后数据")
                
            except Exception as e:
                logger.error(f"计算关键血糖值失败: {e}")
                key_glucose_values = {}
            
            # 生成分析提示词 - 添加调试日志确认数据传递
            logger.info(f"=== AI分析数据传递调试 ===")
            logger.info(f"filtered_glucose_data: {len(filtered_glucose_data) if filtered_glucose_data else 0} 条")
            logger.info(f"treatment_data: {len(treatment_data) if treatment_data else 0} 条")
            logger.info(f"filtered_activity_data: {len(filtered_activity_data) if filtered_activity_data else 0} 条")
            logger.info(f"filtered_meter_data: {len(filtered_meter_data) if filtered_meter_data else 0} 条")
            
            # 详细记录运动和指尖血糖数据
            if filtered_activity_data:
                logger.info("运动数据详情:")
                for i, activity in enumerate(filtered_activity_data[:3]):  # 只显示前3条
                    logger.info(f"  [{i+1}] 时间: {activity.get('shanghai_time', 'N/A')}, 类型: {activity.get('eventType', 'N/A')}, 时长: {activity.get('duration', 0)}分钟")
            
            if filtered_meter_data:
                logger.info("指尖血糖数据详情:")
                for i, meter in enumerate(filtered_meter_data[:3]):  # 只显示前3条
                    logger.info(f"  [{i+1}] 时间: {meter.get('shanghai_time', 'N/A')}, 血糖值: {meter.get('sgv', 'N/A')}")
            
            if treatment_data:
                logger.info("餐食数据详情:")
                meal_count = 0
                for treatment in treatment_data:
                    if treatment.get('eventType') in ['Meal Bolus', 'Snack Bolus']:
                        meal_count += 1
                        # 尝试多个可能的字段名来获取时间戳
                        time_val = treatment.get('shanghai_time') or treatment.get('created_at') or treatment.get('dateString') or treatment.get('timestamp', 'N/A')
                        logger.info(f"  [{meal_count}] 时间: {time_val}, 类型: {treatment.get('eventType', 'N/A')}, 碳水: {treatment.get('carbs', 0)}g")
                logger.info(f"总共检测到 {meal_count} 条餐食记录")
            
            prompt = self.get_analysis_prompt(
                filtered_glucose_data, 
                treatment_data, 
                filtered_activity_data, 
                filtered_meter_data, 
                days, 
                time_window,
                use_smart_range,
                current_time,
                data_completeness,
                key_glucose_values
            )
            
            # 在提示中添加数据质量信息（如果有问题）
            if validation_result["data_quality_score"] < 100:
                quality_warning = f"\n\n**数据质量提醒**：当前数据质量分数为{validation_result['data_quality_score']}分。"
                if validation_result["warnings"]:
                    # 只包含最重要的前2个警告
                    main_warnings = validation_result["warnings"][:2]
                    quality_warning += f"主要问题：{'；'.join(main_warnings)}。"
                quality_warning += "分析结果仅供参考，建议结合更多数据进行判断。"
                prompt += quality_warning
            
            # 获取AI分析结果
            ai_analysis = await self._make_ai_analysis_request(prompt)
            return ai_analysis
            
        except Exception as e:
            logger.error(f"获取AI分析失败: {e}")
            return "AI服务暂时不可用，建议咨询专业医生获得详细指导。"

    def validate_glucose_data(self, glucose_data: List[Dict], treatment_data: List[Dict], activity_data: List[Dict], meter_data: List[Dict]) -> Dict[str, Any]:
        """验证血糖数据的完整性和准确性
        
        Args:
            glucose_data: 血糖数据
            treatment_data: 治疗数据
            activity_data: 活动数据
            meter_data: 指尖血糖数据
            
        Returns:
            包含验证结果和警告信息的字典
        """
        validation_result = {
            "is_valid": True,
            "warnings": [],
            "data_quality_score": 100,
            "missing_data_issues": [],
            "data_consistency_issues": []
        }
        
        # 检查血糖数据基本完整性
        if not glucose_data:
            validation_result["is_valid"] = False
            validation_result["warnings"].append("没有血糖数据可供分析")
            validation_result["data_quality_score"] = 0
            return validation_result
        
        # 检查血糖数据数量是否足够
        if len(glucose_data) < 5:
            validation_result["warnings"].append(f"血糖数据量不足，只有{len(glucose_data)}条记录")
            validation_result["data_quality_score"] -= 30
        
        # 检查血糖数据的数值范围
        invalid_values = []
        for i, entry in enumerate(glucose_data):
            if entry.get("sgv"):
                try:
                    sgv_value = float(entry["sgv"])
                    if sgv_value <= 0 or sgv_value > 600:  # 不合理的血糖值
                        invalid_values.append(f"第{i+1}条记录: {sgv_value} mg/dL")
                except (ValueError, TypeError):
                    invalid_values.append(f"第{i+1}条记录: 无效数值")
        
        if invalid_values:
            validation_result["warnings"].append(f"发现异常血糖值: {', '.join(invalid_values)}")
            validation_result["data_quality_score"] -= 20
        
        # 检查时间戳完整性
        missing_timestamps = []
        for i, entry in enumerate(glucose_data):
            if not entry.get("shanghai_time"):
                missing_timestamps.append(f"第{i+1}条记录")
        
        if missing_timestamps:
            validation_result["warnings"].append(f"缺少时间戳的记录: {', '.join(missing_timestamps)}")
            validation_result["data_quality_score"] -= 15
        
        # 检查数据时间分布
        if glucose_data:
            try:
                # 获取时间范围
                times = []
                for entry in glucose_data:
                    if entry.get("shanghai_time"):
                        times.append(entry["shanghai_time"])
                
                if times:
                    # 检查时间跨度是否合理
                    time_span_hours = self._calculate_data_time_span(times)
                    if time_span_hours < 6:
                        validation_result["warnings"].append(f"数据时间跨度较短，只有{time_span_hours:.1f}小时")
                        validation_result["data_quality_score"] -= 10
                    elif time_span_hours > 48:
                        validation_result["warnings"].append(f"数据时间跨度较长，达到{time_span_hours:.1f}小时")
                    
                    # 检查数据密度
                    data_density = len(glucose_data) / max(time_span_hours, 1)
                    if data_density < 0.5:  # 每小时少于0.5条记录
                        validation_result["warnings"].append(f"数据密度较低，平均每小时{data_density:.1f}条记录")
                        validation_result["data_quality_score"] -= 10
                        
            except Exception as e:
                validation_result["warnings"].append(f"时间分析失败: {str(e)}")
                validation_result["data_quality_score"] -= 5
        
        # 检查指尖血糖数据质量
        if meter_data:
            invalid_meter_values = []
            for i, entry in enumerate(meter_data):
                if entry.get("sgv"):
                    try:
                        meter_value = float(entry["sgv"])
                        if meter_value <= 0 or meter_value > 30:  # mmol/L单位检查
                            invalid_meter_values.append(f"第{i+1}条记录: {meter_value} mmol/L")
                    except (ValueError, TypeError):
                        invalid_meter_values.append(f"第{i+1}条记录: 无效数值")
            
            if invalid_meter_values:
                validation_result["warnings"].append(f"指尖血糖异常值: {', '.join(invalid_meter_values)}")
                validation_result["data_quality_score"] -= 10
        
        # 检查治疗数据质量
        if treatment_data:
            # 检查碳水化合物理性范围
            invalid_carbs = []
            for i, entry in enumerate(treatment_data):
                if entry.get("carbs"):
                    try:
                        carbs_value = float(entry["carbs"])
                        if carbs_value < 0 or carbs_value > 500:  # 不合理的碳水值
                            invalid_carbs.append(f"第{i+1}条记录: {carbs_value}g")
                    except (ValueError, TypeError):
                        invalid_carbs.append(f"第{i+1}条记录: 无效碳水值")
            
            if invalid_carbs:
                validation_result["warnings"].append(f"碳水化合物数据异常: {', '.join(invalid_carbs)}")
                validation_result["data_quality_score"] -= 5
        
        # 确保质量分数在0-100范围内
        validation_result["data_quality_score"] = max(0, min(100, validation_result["data_quality_score"]))
        
        # 如果质量分数低于60，标记为数据质量较差
        if validation_result["data_quality_score"] < 60:
            validation_result["is_valid"] = False
            validation_result["warnings"].append("数据质量较差，建议补充更多数据后再进行分析")
        
        # 记录验证结果
        if validation_result["warnings"]:
            logger.info(f"数据验证结果 - 质量分数: {validation_result['data_quality_score']}, 警告: {len(validation_result['warnings'])}个")
            for warning in validation_result["warnings"]:
                logger.warning(f"数据验证警告: {warning}")
        else:
            logger.info("数据验证通过，无质量问题")
        
        return validation_result
    
    def _calculate_data_time_span(self, time_strings: List[str]) -> float:
        """计算数据时间跨度（小时）
        
        Args:
            time_strings: 时间字符串列表
            
        Returns:
            时间跨度（小时）
        """
        try:
            if not time_strings:
                return 0
            
            # 解析时间字符串
            times = []
            for time_str in time_strings:
                try:
                    # 尝试解析不同格式的时间字符串
                    if "T" in time_str:
                        dt = datetime.fromisoformat(time_str.replace("Z", "+00:00"))
                    else:
                        # 处理 "YYYY-MM-DD HH:MM:SS" 格式
                        dt = datetime.strptime(time_str, "%Y-%m-%d %H:%M:%S")
                    times.append(dt)
                except Exception:
                    continue
            
            if not times:
                return 0
            
            # 计算时间跨度
            time_span = max(times) - min(times)
            return time_span.total_seconds() / 3600  # 转换为小时
            
        except Exception:
            return 0

    def calculate_estimated_hba1c(self, glucose_values_mmol: List[float]) -> Dict[str, float]:
        """计算估算的糖化血红蛋白（HbA1c）"""
        if not glucose_values_mmol:
            return {}

        avg_glucose_mmol = sum(glucose_values_mmol) / len(glucose_values_mmol)
        hba1c_adag_percent = (avg_glucose_mmol + 2.59) / 1.59
        hba1c_nathan_percent = (avg_glucose_mmol + 2.15) / 1.51
        hba1c_adag_mmol = (hba1c_adag_percent - 2.15) * 10.929
        hba1c_nathan_mmol = (hba1c_nathan_percent - 2.15) * 10.929

        return {
            "avg_glucose_mmol": round(avg_glucose_mmol, 1),
            "hba1c_adag_percent": round(hba1c_adag_percent, 1),
            "hba1c_nathan_percent": round(hba1c_nathan_percent, 1),
            "hba1c_adag_mmol": round(hba1c_adag_mmol, 0),
            "hba1c_nathan_mmol": round(hba1c_nathan_mmol, 0)
        }

    def get_hba1c_interpretation(self, hba1c_percent: float) -> str:
        """获取HbA1c值的解释"""
        if hba1c_percent < 5.7:
            return "正常范围"
        elif hba1c_percent < 6.5:
            return "糖尿病前期"
        elif hba1c_percent < 7.0:
            return "糖尿病（控制良好）"
        elif hba1c_percent < 8.0:
            return "糖尿病（控制一般）"
        else:
            return "糖尿病（控制较差）"

    def calculate_glucose_cv(self, glucose_values_mmol: List[float]) -> Dict[str, float]:
        """计算血糖变异系数(CV)"""
        if not glucose_values_mmol or len(glucose_values_mmol) < 2:
            return {}

        mean_glucose = sum(glucose_values_mmol) / len(glucose_values_mmol)
        variance = sum((x - mean_glucose) ** 2 for x in glucose_values_mmol) / len(glucose_values_mmol)
        std_dev = variance ** 0.5
        cv_percent = (std_dev / mean_glucose) * 100

        return {
            "cv_percent": round(cv_percent, 1),
            "std_dev": round(std_dev, 2),
            "mean_glucose": round(mean_glucose, 1)
        }

    def get_cv_interpretation(self, cv_percent: float) -> str:
        """获取血糖变异系数的解释"""
        if cv_percent <= 36:
            return "血糖波动良好"
        elif cv_percent <= 50:
            return "血糖波动一般"
        else:
            return "血糖波动较大"

    def get_basic_statistics(self, glucose_data: List[Dict], treatment_data: List[Dict], days: int = 1) -> str:
        """生成基础统计信息"""
        if not glucose_data:
            return f"📊 {days}天血糖分析报告\n\n❌ 暂无血糖数据可供分析"

        values = []
        for entry in glucose_data:
            if entry.get("sgv"):
                mmol_value = self.mg_dl_to_mmol_l(entry["sgv"])
                values.append(mmol_value)

        if not values:
            return f"📊 {days}天血糖分析报告\n\n❌ 血糖数据格式错误"

        avg_glucose = sum(values) / len(values)
        max_glucose = max(values)
        min_glucose = min(values)

        in_range_count = sum(1 for v in values if 3.9 <= v <= 10.0)
        in_range_percentage = (in_range_count / len(values)) * 100

        hba1c_data = self.calculate_estimated_hba1c(values)
        cv_data = self.calculate_glucose_cv(values)

        carbs_total = 0
        protein_total = 0
        fat_total = 0

        for entry in treatment_data:
            if entry.get("carbs") is not None and entry.get("carbs") > 0:
                carbs_total += entry.get("carbs", 0)

                protein = entry.get("protein")
                if protein is not None:
                    try:
                        protein_value = float(protein) if protein != "" else 0
                        protein_total += protein_value
                    except (ValueError, TypeError):
                        pass

                fat = entry.get("fat")
                if fat is not None:
                    try:
                        fat_value = float(fat) if fat != "" else 0
                        fat_total += fat_value
                    except (ValueError, TypeError):
                        pass

        basic_stats = f"""📊 近{days}日血糖分析

📈 血糖统计：
• 平均血糖：{avg_glucose:.1f} mmol/L
• 最高血糖：{max_glucose:.1f} mmol/L
• 最低血糖：{min_glucose:.1f} mmol/L"""

        if hba1c_data:
            hba1c_percent = hba1c_data["hba1c_adag_percent"]
            hba1c_mmol = hba1c_data["hba1c_adag_mmol"]
            interpretation = self.get_hba1c_interpretation(hba1c_percent)
            basic_stats += f"\n• 糖化血红蛋白估算：{hba1c_percent}% ({hba1c_mmol} mmol/mol) - {interpretation}"

        if cv_data:
            cv_percent = cv_data["cv_percent"]
            cv_interpretation = self.get_cv_interpretation(cv_percent)
            basic_stats += f"\n• 血糖变异系数：{cv_percent}% - {cv_interpretation}"

        basic_stats += f"""
• 目标范围内：{in_range_percentage:.1f}% ({in_range_count}/{len(values)})
• 测量次数：{len(values)}次

🍽️ 饮食统计：
• 总碳水摄入：{carbs_total}g
• 总蛋白质摄入：{protein_total}g
• 总脂肪摄入：{fat_total}g

📋 基础评估："""

        if in_range_percentage >= 70:
            basic_stats += "\n✅ 血糖控制良好，继续保持"
        elif in_range_percentage >= 50:
            basic_stats += "\n⚠️ 血糖控制一般，需要改善"
        else:
            basic_stats += "\n🚨 血糖控制较差，建议咨询医生"

        if max_glucose > 13.9:
            basic_stats += "\n⚠️ 发现高血糖，注意饮食控制"

        if min_glucose < 3.9:
            basic_stats += "\n⚠️ 发现低血糖，注意安全"

        return basic_stats

    def _filter_data_by_time_window(self, data: List[Dict], time_window: int) -> List[Dict]:
        """根据时间窗口过滤数据
        
        Args:
            data: 原始数据列表
            time_window: 时间窗口ID (1, 2, 或 3)
        
        Returns:
            过滤后的数据列表
        """
        if not data:
            return []
        
        filtered_data = []
        time_ranges = {
            1: [(0, 0, 14, 59)],  # 00:00-14:59
            2: [(0, 0, 14, 59), (15, 0, 20, 59)],  # 00:00-14:59 + 15:00-20:59
            3: [(0, 0, 14, 59), (15, 0, 20, 59), (21, 0, 23, 59)]  # 所有时间段
        }
        
        for entry in data:
            shanghai_time = entry.get("shanghai_time", "")
            if not shanghai_time or len(shanghai_time) < 16:
                continue
                
            try:
                # 解析时间字符串，格式为 "YYYY-MM-DD HH:MM"
                time_part = shanghai_time[:16]
                dt = datetime.strptime(time_part, "%Y-%m-%d %H:%M")
                hour = dt.hour
                minute = dt.minute
                
                # 检查是否在指定的时间范围内
                for start_hour, start_minute, end_hour, end_minute in time_ranges.get(time_window, []):
                    if (start_hour < hour < end_hour) or \
                       (hour == start_hour and minute >= start_minute) or \
                       (hour == end_hour and minute <= end_minute):
                        filtered_data.append(entry)
                        break
                        
            except (ValueError, TypeError):
                continue
        
        return filtered_data

    def _check_data_availability(self, glucose_data: List[Dict], treatment_data: List[Dict], 
                               activity_data: List[Dict], meter_data: List[Dict], 
                               time_window: int) -> Dict[str, Any]:
        """检查各时间段数据可用性
        
        Args:
            glucose_data: 血糖数据
            treatment_data: 治疗数据
            activity_data: 活动数据
            meter_data: 指尖血糖数据
            time_window: 时间窗口ID
        
        Returns:
            各时间段数据可用性字典
        """
        availability = {}
        
        # 检查每个时间段的数据
        for window_id in [1, 2, 3]:
            window_glucose = self._filter_data_by_time_window(glucose_data, window_id)
            window_treatment = self._filter_data_by_time_window(treatment_data, window_id)
            window_activity = self._filter_data_by_time_window(activity_data, window_id)
            window_meter = self._filter_data_by_time_window(meter_data, window_id)
            
            availability[f"window_{window_id}"] = {
                "has_glucose": len(window_glucose) > 0,
                "has_treatment": len(window_treatment) > 0,
                "has_activity": len(window_activity) > 0,
                "has_meter": len(window_meter) > 0,
                "has_any_data": len(window_glucose + window_treatment + window_activity + window_meter) > 0
            }
        
        return availability

    def _generate_data_availability_message(self, availability: Dict[str, Any], current_window: int) -> str:
        """生成数据可用性消息
        
        Args:
            availability: 数据可用性字典
            current_window: 当前时间窗口ID
        
        Returns:
            数据可用性消息字符串
        """
        if not availability:
            return ""
        
        messages = []
        
        # 检查当前时间段的数据
        current_key = f"window_{current_window}"
        current_data = availability.get(current_key, {})
        
        if not current_data.get("has_any_data", False):
            messages.append(f"警告：缺乏时间段{current_window}的数据，无法进行完整分析")
        
        # 检查关键数据类型
        if not current_data.get("has_glucose", False):
            messages.append(f"警告：时间段{current_window}缺乏血糖数据")
        
        if current_window in [1, 2] and not current_data.get("has_treatment", False):
            messages.append(f"警告：时间段{current_window}缺乏餐食记录")
        
        if messages:
            return "\n**数据可用性警告：**\n" + "\n".join(f"* {msg}" for msg in messages)
        else:
            return "\n**数据可用性：** 该时间段数据完整，可以进行分析"

    def _generate_smart_analysis_guidance(self, analysis_time: datetime, data_completeness: Dict[str, Any] = None) -> str:
        """生成智能分析指导"""
        try:
            current_hour = analysis_time.hour
            
            # 基于当前时间生成分析重点
            if current_hour < 6:
                guidance = "**分析重点时段：凌晨空腹期**\n"
                guidance += "- 重点分析凌晨血糖控制情况和黎明现象\n"
                guidance += "- 关注空腹血糖的稳定性和趋势\n"
                guidance += "- 评估夜间基础胰岛素效果（如有）"
            elif current_hour < 12:
                guidance = "**分析重点时段：早餐后上午期**\n"
                guidance += "- 重点分析早餐对血糖的影响\n"
                guidance += "- 关注早餐后血糖峰值和持续时间\n"
                guidance += "- 评估上午血糖的稳定性变化"
            elif current_hour < 18:
                guidance = "**分析重点时段：午餐后下午期**\n"
                guidance += "- 重点分析午餐对血糖的影响\n"
                guidance += "- 关注下午血糖波动模式\n"
                guidance += "- 评估运动对下午血糖的影响（如有运动数据）"
            else:
                guidance = "**分析重点时段：晚餐后夜间期**\n"
                guidance += "- 重点分析晚餐对血糖的影响\n"
                guidance += "- 关注晚餐后血糖控制情况\n"
                guidance += "- 评估睡前血糖安全性"
            
            # 基于数据完整性添加额外指导
            if data_completeness:
                completeness_score = data_completeness.get('completeness_score', 100)
                if completeness_score < 70:
                    guidance += "\n\n**数据完整性提醒：**\n"
                    guidance += "- 当前数据完整性较低，分析结果可能不够准确\n"
                    guidance += "- 请重点关注已有数据的趋势，避免过度解读\n"
                    guidance += "- 建议补充更多数据后重新分析"
                
                missing_ranges = data_completeness.get('missing_time_ranges', [])
                if missing_ranges:
                    guidance += "\n\n**缺失数据提醒：**\n"
                    guidance += "- 分析中存在数据缺失时段，请注意相关结论的局限性\n"
                    guidance += "- 重点关注数据完整时段的血糖控制情况"
            
            return guidance
            
        except Exception as e:
            logger.error(f"生成智能分析指导失败: {e}")
            return "**分析重点：当前时间段血糖控制情况**"

    def get_analysis_prompt(self, glucose_data: List[Dict], treatment_data: List[Dict], activity_data: List[Dict], meter_data: List[Dict], days: int = 1, time_window: int = None, use_smart_range: bool = True, analysis_time: datetime = None, data_completeness: Dict[str, Any] = None, key_glucose_values: Dict[str, Any] = None) -> str:
        """生成AI分析的prompt - 支持动态数据范围分析
        
        Args:
            glucose_data: 血糖数据
            treatment_data: 治疗数据
            activity_data: 活动数据
            meter_data: 指尖血糖数据
            days: 分析天数
            time_window: 时间窗口ID (1, 2, 或 3)，保持向后兼容性
            use_smart_range: 是否使用智能数据范围，默认为True
            analysis_time: 分析时间，默认为当前时间
            data_completeness: 数据完整性检查结果
            key_glucose_values: 关键血糖值（空腹和餐后血糖）
        """
        
        # 使用配置的时区偏移（和shanghai_time字段保持一致）
        timezone_offset = self._get_valid_timezone_offset()
        timezone_name = self._format_timezone_name(timezone_offset)
        
        # 生成动态数据范围分析提示
        dynamic_range_info = ""
        completeness_info = ""
        current_time_info = ""
        
        # 生成关键血糖值信息
        key_glucose_info = ""
        if key_glucose_values:
            key_glucose_parts = []
            
            # 空腹血糖信息
            if key_glucose_values.get('fasting'):
                fasting_data = key_glucose_values['fasting']
                fasting_info = f"空腹血糖(6:00 AM): 平均{fasting_data['average']:.1f} mmol/L, 最新{fasting_data['latest']:.1f} mmol/L, 测量次数{fasting_data['count']}次"
                key_glucose_parts.append(fasting_info)
            
            # 餐后血糖信息
            if key_glucose_values.get('postprandial'):
                postprandial_data = key_glucose_values['postprandial']
                for meal_type, meal_info in postprandial_data.items():
                    if meal_info and isinstance(meal_info, dict) and meal_info.get('values'):
                        meal_names = {'breakfast': '早餐后', 'lunch': '午餐后', 'dinner': '晚餐后'}
                        meal_name = meal_names.get(meal_type, meal_type)
                        postprandial_info = f"{meal_name}2小时血糖: 平均{meal_info['average']:.1f} mmol/L, 最新{meal_info['latest']:.1f} mmol/L, 测量次数{meal_info['count']}次"
                        key_glucose_parts.append(postprandial_info)
            
            if key_glucose_parts:
                key_glucose_info = f"\n**关键血糖值分析**：\n" + "\n".join(f"• {part}" for part in key_glucose_parts) + "\n"
        
        # 获取动态数据范围信息
        if analysis_time is None:
            analysis_time = self._now_in_config_timezone()
        
        data_range = self.get_dynamic_data_range(analysis_time)
        dynamic_range_info = f"**动态数据范围分析**：{data_range['range_description']}（预期时长：{data_range['expected_duration_hours']:.1f}小时）"
        
        # 添加当前时间信息和餐食时间判断逻辑
        current_time_info = f"**当前时间**：{data_range['current_time_for_ai']}"
        current_time_info += f"\n**时区信息**：{data_range['timezone_info']}"
        current_time_info += f"\n**数据截止时间**：当日00:00至当前时间，数据分析基于此时间范围内的全部数据。"
        
        # 添加数据完整性信息
        if data_completeness:
            completeness_score = data_completeness.get('completeness_score', 100)
            # 对于动态数据范围，不再显示缺失时间段（数据截止到当前时间是正常的）
            missing_ranges = []  # 清空缺失时间段
            
            completeness_info = f"\n**数据完整性评分**：{completeness_score}分"
            
            if data_completeness.get('warnings'):
                warnings = data_completeness.get('warnings', [])
                completeness_info += f"\n**数据警告**：{'; '.join(warnings[:2])}"
        
        # 转换血糖数据为mmol/L并转换时区
        glucose_mmol = []
        for entry in glucose_data:
            if entry.get("sgv"):
                mmol_value = self.mg_dl_to_mmol_l(entry["sgv"])
                shanghai_time = entry.get("shanghai_time", "")
                if shanghai_time and len(shanghai_time) >= 16:
                    shanghai_time = shanghai_time[:16]
                glucose_mmol.append({
                    "time": shanghai_time,
                    "value": mmol_value,
                    "direction": entry.get("direction", ""),
                    "trend": entry.get("trend", 0)
                })

        # 转换指尖血糖数据（指尖血糖数据已经是mmol/L单位，无需转换）
        meter_mmol = []
        for entry in meter_data:
            if entry.get("sgv"):
                # 指尖血糖数据已经是mmol/L单位，直接使用
                mmol_value = float(entry["sgv"])
                shanghai_time = entry.get("shanghai_time", "")
                if shanghai_time and len(shanghai_time) >= 16:
                    shanghai_time = shanghai_time[:16]
                meter_mmol.append({
                    "time": shanghai_time,
                    "value": mmol_value
                })

        # 分析餐食和营养数据
        meals = []
        carbs_total = 0
        protein_total = 0
        fat_total = 0
        
        # 餐食类型分类统计
        meal_type_summary = {
            'Meal Bolus': {'count': 0, 'carbs': 0, 'times': []},
            'Snack Bolus': {'count': 0, 'carbs': 0, 'times': []},
            'Correction Bolus': {'count': 0, 'carbs': 0, 'times': []},
            'Other': {'count': 0, 'carbs': 0, 'times': []}
        }

        for entry in treatment_data:
            carbs = entry.get("carbs")
            protein = entry.get("protein")
            fat = entry.get("fat")
            event_type = entry.get("eventType", "")

            shanghai_time = entry.get("shanghai_time", "")
            if shanghai_time and len(shanghai_time) >= 16:
                shanghai_time = shanghai_time[:16]

            if carbs is not None and carbs > 0:
                carbs_total += carbs

                protein_value = 0
                fat_value = 0

                if protein is not None:
                    try:
                        protein_value = float(protein) if protein != "" else 0
                        protein_total += protein_value
                    except (ValueError, TypeError):
                        protein_value = 0

                if fat is not None:
                    try:
                        fat_value = float(fat) if fat != "" else 0
                        fat_total += fat_value
                    except (ValueError, TypeError):
                        fat_value = 0

                # 添加到餐食列表
                meals.append({
                    "time": shanghai_time,
                    "carbs": carbs,
                    "protein": protein_value,
                    "fat": fat_value,
                    "notes": entry.get("notes", ""),
                    "event_type": event_type
                })
                
                # 统计餐食类型
                if event_type in meal_type_summary:
                    meal_type_summary[event_type]['count'] += 1
                    meal_type_summary[event_type]['carbs'] += carbs
                    meal_type_summary[event_type]['times'].append(shanghai_time)
                else:
                    meal_type_summary['Other']['count'] += 1
                    meal_type_summary['Other']['carbs'] += carbs
                    meal_type_summary['Other']['times'].append(shanghai_time)

        # 添加餐食时间判断逻辑（在实际餐食数据处理之后）
        current_hour = data_range['end_time'].hour
        meal_time_analysis = ""
        
        # 基于实际餐食数据和分析时间生成更准确的分析时段说明
        def is_time_in_range(time_str, start_time, end_time):
            """检查时间是否在指定范围内"""
            try:
                # 处理完整的时间格式 "2025-08-13 07:37" 或简化格式 "07:37"
                if ' ' in time_str:
                    time_part = time_str.split(' ')[1]  # 提取时间部分
                else:
                    time_part = time_str
                
                time_hour = int(time_part.split(':')[0])
                time_minute = int(time_part.split(':')[1])
                start_hour = int(start_time.split(':')[0])
                start_minute = int(start_time.split(':')[1])
                end_hour = int(end_time.split(':')[0])
                end_minute = int(end_time.split(':')[1])
                
                total_minutes = time_hour * 60 + time_minute
                start_total = start_hour * 60 + start_minute
                end_total = end_hour * 60 + end_minute
                
                return start_total <= total_minutes <= end_total
            except (ValueError, IndexError):
                return False
        
        has_breakfast = any(1 for m in meals if m['event_type'] in ['Meal Bolus', 'Snack Bolus'] and is_time_in_range(m['time'], '06:00', '10:00'))
        has_lunch = any(1 for m in meals if m['event_type'] in ['Meal Bolus', 'Snack Bolus'] and is_time_in_range(m['time'], '11:00', '15:00'))
        has_dinner = any(1 for m in meals if m['event_type'] in ['Meal Bolus', 'Snack Bolus'] and is_time_in_range(m['time'], '17:00', '21:00'))
        
        if current_hour < 10:
            available_meals = []
            if has_breakfast:
                available_meals.append("早餐")
            meal_time_analysis = f"\n**餐食分析时段**：当前时间为早晨，主要分析早餐时段数据。"
            if available_meals:
                meal_time_analysis += f"已有{', '.join(available_meals)}数据可供分析。"
            else:
                meal_time_analysis += "当前暂无早餐数据，如有加餐数据将一并分析。"
        elif current_hour < 15:
            available_meals = []
            if has_breakfast:
                available_meals.append("早餐")
            if has_lunch:
                available_meals.append("午餐")
            meal_time_analysis = f"\n**餐食分析时段**：当前时间为中午。"
            if available_meals:
                meal_time_analysis += f"已有{', '.join(available_meals)}数据可供分析。"
            else:
                meal_time_analysis += "当前暂无主要餐次数据，如有加餐数据将一并分析。"
        elif current_hour < 20:
            available_meals = []
            if has_breakfast:
                available_meals.append("早餐")
            if has_lunch:
                available_meals.append("午餐")
            if has_dinner:
                available_meals.append("晚餐")
            meal_time_analysis = f"\n**餐食分析时段**：当前时间为下午。"
            if available_meals:
                meal_time_analysis += f"已有{', '.join(available_meals)}数据可供分析。"
            else:
                meal_time_analysis += "当前暂无主要餐次数据，如有加餐数据将一并分析。"
        else:
            available_meals = []
            if has_breakfast:
                available_meals.append("早餐")
            if has_lunch:
                available_meals.append("午餐")
            if has_dinner:
                available_meals.append("晚餐")
            meal_time_analysis = f"\n**餐食分析时段**：当前时间为晚上，可分析全天数据。"
            if available_meals:
                meal_time_analysis += f"已有{', '.join(available_meals)}数据可供分析。"
            else:
                meal_time_analysis += "当前暂无主要餐次数据，如有加餐数据将一并分析。"
        
        current_time_info += meal_time_analysis

        # 分析运动数据
        activities = []
        total_duration = 0
        for entry in activity_data:
            shanghai_time = entry.get("shanghai_time", "")
            if shanghai_time and len(shanghai_time) >= 16:
                shanghai_time = shanghai_time[:16]
            
            duration = entry.get("duration", 0)
            total_duration += duration
            
            activities.append({
                "time": shanghai_time,
                "event_type": entry.get("eventType", ""),
                "duration": duration,
                "notes": entry.get("notes", "")
            })

        bmi_data = self.calculate_bmi()
        body_fat = self.config.get("basic", {}).get("body_fat_percentage", 0)
        
        # 获取治疗方案数据
        treatment_plan = self.config.get("treatment_plan", {})
        medications = treatment_plan.get("medications", [])
        insulin_enabled = treatment_plan.get("insulin_enabled", False)
        insulin_dosage = treatment_plan.get("insulin_dosage", 0)
        insulin_frequency = treatment_plan.get("insulin_frequency", "")
        insulin_custom_frequency = treatment_plan.get("insulin_custom_frequency", "")
        
        personal_info = []
        if bmi_data.get("bmi") > 0:
            personal_info.append(f"用户BMI为 {bmi_data['bmi']} ({bmi_data['status']})")
        if body_fat > 0:
            personal_info.append(f"体脂率为 {body_fat}%")
        
        # 添加治疗方案信息
        treatment_info = []
        if medications:
            medication_list = []
            for med in medications:
                med_info = f"{med['name']} {med['dosage']}{med['unit']}"
                if med['usage']:
                    med_info += f" ({med['usage']})"
                medication_list.append(med_info)
            treatment_info.append(f"药物治疗: {', '.join(medication_list)}")
        
        if insulin_enabled:
            insulin_info = f"胰岛素治疗: 每次{insulin_dosage}单位, "
            if insulin_frequency == "custom" and insulin_custom_frequency:
                insulin_info += insulin_custom_frequency
            elif insulin_frequency:
                insulin_info += insulin_frequency
            else:
                insulin_info += "频率未设置"
            treatment_info.append(insulin_info)
        
        prompt_info = " ".join(personal_info)
        treatment_prompt = " ".join(treatment_info) if treatment_info else "无特殊治疗方案"

        # 使用动态数据范围分析
        current_time = analysis_time or self._now_in_config_timezone()
        today_date = current_time.strftime("%m月%d日")
        
        # 生成智能分析指导
        analysis_guidance = self._generate_smart_analysis_guidance(current_time, data_completeness)
        
        prompt = f"""
你是一位专业的内分泌科医生和糖尿病管理专家。请分析以下{days}天的血糖监测数据，使用动态数据范围分析方法。{prompt_info} {treatment_prompt}

{dynamic_range_info}
{current_time_info}
{completeness_info}
{analysis_guidance}

**动态分析要求：**
- 基于从当天00:00到当前分析时间（{data_range['end_time_str']}）的全量数据进行分析
- 当前时间为{data_range['current_time_for_ai']}，所有分析都基于此时间点之前的数据
- 重点分析该动态时间段内的血糖控制情况和趋势
- 识别数据缺失对分析准确性的影响
- 结合治疗数据和活动数据提供综合分析
- 基于当前动态时间段提供针对性的改善建议

**数据完整性考虑：**
- 当前分析基于从00:00到当前时间的完整数据，不存在数据缺失问题
- 当前时间之后的数据尚未产生，这是正常的时间进程而非数据缺失
- 重点分析现有数据的模式和趋势，不要讨论"缺失数据"或"未提供数据"

请提供以下针对性分析：
1. 当前动态时间段血糖控制状况评估
2. 血糖波动模式和趋势分析
3. 餐后血糖反应评估（根据当前时间判断可分析的餐次）：
   - 如果当前时间<10点：重点分析早餐后血糖反应
   - 如果当前时间10-15点：分析早餐和午餐后血糖反应
   - 如果当前时间15-20点：分析早餐、午餐和晚餐后血糖反应
   - 如果当前时间≥20点：分析全天的早餐、午餐、晚餐后血糖反应
4. 基于现有数据的综合分析
5. 基于当前动态时间段的改善建议

{key_glucose_info}

**重要提醒：**
- 所有提到的血糖数值必须明确区分是实际测量还是AI推理/预测
- 如果使用任何非实际测量的数值进行推测，必须明确标注数据来源
- 避免将AI推理数据呈现为实际测量结果
- 分析结论仅基于当前时间范围内已产生的数据

请用专业但易懂的语言回答。"""

        # 添加血糖数据
        prompt += f"\n注意：所有时间显示均为用户本地时间（{timezone_name}），请基于此时区进行分析。\n\n"
        prompt += "**重要要求：数据来源透明度**\n"
        prompt += "- 必须明确区分实际测量的血糖数据和AI推理/预测的数值\n"
        prompt += "- 对于任何非实际测量数据（如预测值、估算值、插值等），必须明确标注为\"AI预测\"、\"估算值\"或\"推理数据\"\n"
        prompt += "- 例如：如果提到8.9 mmol/L这个数值，必须说明是实际测量还是AI推理得到\n"
        prompt += "- 不得将AI推理数据混同为实际测量数据进行报告\n\n"
        
        prompt += "血糖数据（mmol/L）：\n"

        # 添加血糖数据（传递所有血糖数据，确保AI能看到完整的时间段）
        for entry in glucose_mmol:
            direction_symbol = {
                "Flat": "→",
                "FortyFiveUp": "↗",
                "SingleUp": "↑",
                "DoubleUp": "↑↑",
                "FortyFiveDown": "↘",
                "SingleDown": "↓",
                "DoubleDown": "↓↓"
            }.get(entry["direction"], "")

            prompt += f"• {entry['time']}: {entry['value']} mmol/L {direction_symbol}\n"

        # 添加指尖血糖数据（传递所有指尖血糖数据）
        if meter_mmol:
            prompt += f"\n指尖血糖数据（mmol/L）：\n"
            for entry in meter_mmol:
                prompt += f"• {entry['time']}: {entry['value']} mmol/L\n"

        if meals:
            prompt += f"\n餐食记录（总碳水: {carbs_total}g, 总蛋白质: {protein_total}g, 总脂肪: {fat_total}g）：\n"
            
            # 添加餐食类型分析
            meal_summary_parts = []
            for meal_type, summary in meal_type_summary.items():
                if summary['count'] > 0:
                    type_name = {
                        'Meal Bolus': '正餐',
                        'Snack Bolus': '加餐',
                        'Correction Bolus': '校正',
                        'Other': '其他'
                    }.get(meal_type, meal_type)
                    meal_summary_parts.append(f"{type_name}{summary['count']}次({summary['carbs']}g碳水)")
            
            if meal_summary_parts:
                prompt += f"餐食类型分析：{', '.join(meal_summary_parts)}\n"

            for meal in meals:
                event_info = f"[{meal['event_type']}]" if meal['event_type'] else ""
                notes_info = f" - {meal['notes']}" if meal['notes'] else ""

                nutrition_parts = [f"{meal['carbs']}g碳水"]
                if meal['protein'] > 0:
                    nutrition_parts.append(f"{meal['protein']}g蛋白质")
                if meal['fat'] > 0:
                    nutrition_parts.append(f"{meal['fat']}g脂肪")
                nutrition_info = ", ".join(nutrition_parts)

                prompt += f"• {meal['time']}: {nutrition_info} {event_info}{notes_info}\n"
                
            # 添加餐食时间分析
            def is_time_in_range_meal(time_str, start_time, end_time):
                """检查时间是否在指定范围内"""
                try:
                    # 处理完整的时间格式 "2025-08-13 07:37" 或简化格式 "07:37"
                    if ' ' in time_str:
                        time_part = time_str.split(' ')[1]  # 提取时间部分
                    else:
                        time_part = time_str
                    
                    time_hour = int(time_part.split(':')[0])
                    time_minute = int(time_part.split(':')[1])
                    start_hour = int(start_time.split(':')[0])
                    start_minute = int(start_time.split(':')[1])
                    end_hour = int(end_time.split(':')[0])
                    end_minute = int(end_time.split(':')[1])
                    
                    total_minutes = time_hour * 60 + time_minute
                    start_total = start_hour * 60 + start_minute
                    end_total = end_hour * 60 + end_minute
                    
                    return start_total <= total_minutes <= end_total
                except (ValueError, IndexError):
                    return False
            
            breakfast_times = [m['time'] for m in meals if m['event_type'] in ['Meal Bolus', 'Snack Bolus'] and is_time_in_range_meal(m['time'], '06:00', '10:00')]
            lunch_times = [m['time'] for m in meals if m['event_type'] in ['Meal Bolus', 'Snack Bolus'] and is_time_in_range_meal(m['time'], '11:00', '15:00')]
            dinner_times = [m['time'] for m in meals if m['event_type'] in ['Meal Bolus', 'Snack Bolus'] and is_time_in_range_meal(m['time'], '17:00', '21:00')]
            
            meal_analysis_parts = []
            if breakfast_times:
                meal_analysis_parts.append(f"早餐时段({min(breakfast_times)}-{max(breakfast_times)})")
            if lunch_times:
                meal_analysis_parts.append(f"午餐时段({min(lunch_times)}-{max(lunch_times)})")
            if dinner_times:
                meal_analysis_parts.append(f"晚餐时段({min(dinner_times)}-{max(dinner_times)})")
            
            if meal_analysis_parts:
                prompt += f"\n餐食时段覆盖：{'、'.join(meal_analysis_parts)}\n"
            else:
                prompt += f"\n餐食时段覆盖：无主要餐次记录\n"
        else:
            prompt += f"\n餐食记录：无碳水摄入记录\n"

        # 添加运动数据
        if activities:
            prompt += f"\n运动记录（总时长: {total_duration}分钟）：\n"
            for activity in activities:
                event_info = f"[{activity['event_type']}]" if activity['event_type'] else ""
                notes_info = f" - {activity['notes']}" if activity['notes'] else ""
                prompt += f"• {activity['time']}: {activity['duration']}分钟 {event_info}{notes_info}\n"
        else:
            prompt += f"\n运动记录：无运动记录\n"

        # 添加餐后血糖信息
        post_meal_info = self._generate_post_meal_glucose_info(glucose_data, treatment_data)
        if post_meal_info:
            prompt += "\n**餐后血糖详细信息：**\n"
            for i, info in enumerate(post_meal_info, 1):
                prompt += f"""
{i}. ★餐后血糖
   口餐后血糖：{info['glucose_value']}mmoL
   对应餐食：{info['carbs']}g碳水
   餐食类型：{info['event_type']}
   餐食时间：{info['meal_time']}
   测量时间：{info['glucose_time']}
   餐后时间：{info['time_diff_hours']}小时"""
                if info.get('notes'):
                    prompt += f"\n   备注：{info['notes']}"
        else:
            prompt += "\n**餐后血糖详细信息：**\n   暂无餐后血糖数据\n"

        # 计算统计数据
        if glucose_mmol:
            values = [entry["value"] for entry in glucose_mmol]
            avg_glucose = sum(values) / len(values)
            max_glucose = max(values)
            min_glucose = min(values)

            in_range_count = sum(1 for v in values if 3.9 <= v <= 10.0)
            in_range_percentage = (in_range_count / len(values)) * 100

            # 添加时间窗口特定的分析指导
            time_window_guidance = ""
            if time_window is not None:
                current_time = datetime.now()
                # 获取当前日期和时间范围
                today_date = current_time.strftime("%m月%d日")
                time_ranges = {
                    1: "00:00-14:59",
                    2: "15:00-20:59", 
                    3: "21:00-23:59"
                }
                time_range = time_ranges.get(time_window, "00:00-23:59")
                
                time_descriptions = {
                    1: f"""根据你{today_date} {time_range}的各种数据分析，请重点关注：
- 凌晨空腹血糖控制情况
- 早餐后血糖反应和峰值
- 上午血糖稳定性
- 如有午餐数据，请分析午餐前血糖准备情况
- 识别和报告该时间段内任何缺失的关键数据""",
                    2: f"""根据你{today_date} {time_range}的各种数据分析，请重点关注：
- 午餐后血糖反应和控制
- 下午血糖波动模式
- 晚餐前血糖准备情况
- 如有晚餐数据，请分析晚餐后初步反应
- 运动对下午血糖的影响
- 识别和报告该时间段内任何缺失的关键数据""",
                    3: f"""根据你{today_date} {time_range}的各种数据分析，请重点关注：
- 晚餐后血糖反应和控制
- 夜间血糖起始水平和趋势
- 全天血糖控制总结
- 睡前血糖安全性评估
- 基于全天数据的整体改善建议
- 识别和报告任何缺失的关键数据（特别是晚餐数据）"""
                }
                time_window_guidance = time_descriptions.get(time_window, f"根据你{today_date} {time_range}的各种数据分析：")
            else:
                # 如果没有指定时间窗口，使用默认的当天数据分析格式
                current_time = datetime.now()
                today_date = current_time.strftime("%m月%d日")
                time_window_guidance = f"根据你{today_date} 00:00-23:59的各种数据分析："
            
            prompt += f"""

统计数据：
• 平均血糖：{avg_glucose:.1f} mmol/L
• 最高血糖：{max_glucose:.1f} mmol/L
• 最低血糖：{min_glucose:.1f} mmol/L
• 目标范围内比例：{in_range_percentage:.1f}% ({in_range_count}/{len(values)})
• 总测量次数：{len(values)}次
• 指尖血糖记录：{len(meter_mmol)}次
• 运动记录：{len(activities)}次
{key_glucose_info}
{time_window_guidance}

请提供以下分析：
1. 血糖控制状况评估
2. 血糖波动模式分析
3. 餐后血糖反应评估
4. 营养摄入分析
5. 运动对血糖的影响分析
6. 具体的改善建议
7. 需要关注的风险点

**重要提醒：**
- 所有提到的血糖数值必须明确区分是实际测量还是AI推理/预测
- 如果使用任何非实际测量的数值进行推测，必须明确标注数据来源
- 避免将AI推理数据呈现为实际测量结果

请用专业但易懂的语言回答。"""

        # 计算统计数据
        if glucose_mmol:
            values = [entry["value"] for entry in glucose_mmol]
            avg_glucose = sum(values) / len(values)
            max_glucose = max(values)
            min_glucose = min(values)

            in_range_count = sum(1 for v in values if 3.9 <= v <= 10.0)
            in_range_percentage = (in_range_count / len(values)) * 100

            prompt += f"""

统计数据：
• 平均血糖：{avg_glucose:.1f} mmol/L
• 最高血糖：{max_glucose:.1f} mmol/L
• 最低血糖：{min_glucose:.1f} mmol/L
• 目标范围内比例：{in_range_percentage:.1f}% ({in_range_count}/{len(values)})
• 总测量次数：{len(values)}次
• 指尖血糖记录：{len(meter_mmol)}次
• 运动记录：{len(activities)}次"""

        return prompt

    @ai_retry_decorator(max_retries=3)
    async def _make_ai_consultation_request(self, prompt: str) -> str:
        """执行AI咨询HTTP请求（带有重试机制）"""
        request_data = {
            "model": self.config["ai_config"]["model_name"],
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.7,
            "top_p": 0.9,
            "max_tokens": 16000,
            "stream": False
        }

        headers = {
            "Content-Type": "application/json"
        }

        if self.config["ai_config"]["api_key"]:
            headers["Authorization"] = f"Bearer {self.config['ai_config']['api_key']}"

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.config["ai_config"]["timeout"])) as session:
            async with session.post(self.config["ai_config"]["api_url"], json=request_data, headers=headers) as response:
                if response.status == 200:
                    result = await response.json()
                    if 'choices' in result and len(result['choices']) > 0:
                        ai_response = result['choices'][0]['message']['content'].strip()
                        return ai_response
                    else:
                        raise ValueError(f"AI响应格式错误: {result}")
                else:
                    error_text = await response.text()
                    raise Exception(f"AI请求HTTP错误: {response.status} - {error_text}")

    async def get_ai_consultation(self, question: str, include_data: bool, days: int = 1) -> str:
        """获取AI咨询结果"""
        try:
            glucose_data = []
            treatment_data = []
            activity_data = []
            meter_data = []

            if include_data:
                glucose_data = self.get_glucose_data_from_db(days)
                treatment_data = self.get_treatment_data_from_db(days)
                activity_data = self.get_activity_data_from_db(days)
                meter_data = self.get_meter_data_from_db(days)

                if not glucose_data:
                    return "抱歉，没有足够的血糖数据来进行咨询。请先同步数据。"
                
                # 数据验证 - 只有在包含数据时才进行验证
                validation_result = self.validate_glucose_data(glucose_data, treatment_data, activity_data, meter_data)
                
                # 如果数据验证失败，返回相应的错误信息
                if not validation_result["is_valid"]:
                    if validation_result["data_quality_score"] == 0:
                        return "没有可用的血糖数据进行分析，请先同步数据。"
                    else:
                        warnings_summary = "；".join(validation_result["warnings"][:2])  # 限制显示前2个警告
                        return f"数据质量较差，无法进行准确咨询：{warnings_summary}。建议补充更多数据后再试。"
                
                # 记录数据质量信息
                if validation_result["data_quality_score"] < 80:
                    logger.warning(f"AI咨询数据质量分数较低: {validation_result['data_quality_score']}")

            prompt = self.get_consultation_prompt(question, glucose_data, treatment_data, activity_data, meter_data, days, include_data)
            
            # 在提示中添加数据质量信息（如果有问题且包含数据）
            if include_data and 'validation_result' in locals() and validation_result.get("data_quality_score", 100) < 100:
                quality_warning = f"\n\n**数据质量提醒**：当前数据质量分数为{validation_result['data_quality_score']}分。"
                if validation_result["warnings"]:
                    main_warnings = validation_result["warnings"][:2]
                    quality_warning += f"主要问题：{'；'.join(main_warnings)}。"
                quality_warning += "咨询结果仅供参考，建议结合更多数据进行判断。"
                prompt += quality_warning
            
            ai_response = await self._make_ai_consultation_request(prompt)
            return ai_response

        except Exception as e:
            logger.error(f"获取AI咨询失败: {e}")
            return "AI服务暂时不可用，建议咨询专业医生获得详细指导。"

    def get_consultation_prompt(self, question: str, glucose_data: List[Dict], treatment_data: List[Dict], activity_data: List[Dict], meter_data: List[Dict], days: int, include_data: bool) -> str:
        """生成AI咨询的prompt"""
        # 获取用户配置的时区偏移
        timezone_offset = self.config.get("basic", {}).get("timezone_offset", 8)
        timezone_name = f"UTC+{timezone_offset}" if timezone_offset >= 0 else f"UTC{timezone_offset}"
        
        bmi_data = self.calculate_bmi()
        body_fat = self.config.get("basic", {}).get("body_fat_percentage", 0)
        
        # 获取治疗方案数据
        treatment_plan = self.config.get("treatment_plan", {})
        medications = treatment_plan.get("medications", [])
        insulin_enabled = treatment_plan.get("insulin_enabled", False)
        insulin_dosage = treatment_plan.get("insulin_dosage", 0)
        insulin_frequency = treatment_plan.get("insulin_frequency", "")
        insulin_custom_frequency = treatment_plan.get("insulin_custom_frequency", "")

        personal_info = []
        if bmi_data.get("bmi") > 0:
            personal_info.append(f"用户BMI为 {bmi_data['bmi']} ({bmi_data['status']})")
        if body_fat > 0:
            personal_info.append(f"体脂率为 {body_fat}%")
        
        # 添加治疗方案信息
        treatment_info = []
        if medications:
            medication_list = []
            for med in medications:
                med_info = f"{med['name']} {med['dosage']}{med['unit']}"
                if med['usage']:
                    med_info += f" ({med['usage']})"
                medication_list.append(med_info)
            treatment_info.append(f"药物治疗: {', '.join(medication_list)}")
        
        if insulin_enabled:
            insulin_info = f"胰岛素治疗: 每次{insulin_dosage}单位, "
            if insulin_frequency == "custom" and insulin_custom_frequency:
                insulin_info += insulin_custom_frequency
            elif insulin_frequency:
                insulin_info += insulin_frequency
            else:
                insulin_info += "频率未设置"
            treatment_info.append(insulin_info)
            
        prompt_info = " ".join(personal_info)
        treatment_prompt = " ".join(treatment_info) if treatment_info else "无特殊治疗方案"

        if include_data:
            glucose_mmol = []
            for entry in glucose_data:
                if entry.get("sgv"):
                    mmol_value = self.mg_dl_to_mmol_l(entry["sgv"])
                    shanghai_time = entry.get("shanghai_time", "")
                    if shanghai_time and len(shanghai_time) >= 16:
                        shanghai_time = shanghai_time[:16]
                    glucose_mmol.append({
                        "time": shanghai_time,
                        "value": mmol_value
                    })

            # 转换指尖血糖数据（指尖血糖数据已经是mmol/L单位，无需转换）
            meter_mmol = []
            for entry in meter_data:
                if entry.get("sgv"):
                    # 指尖血糖数据已经是mmol/L单位，直接使用
                    mmol_value = float(entry["sgv"])
                    shanghai_time = entry.get("shanghai_time", "")
                    if shanghai_time and len(shanghai_time) >= 16:
                        shanghai_time = shanghai_time[:16]
                    meter_mmol.append({
                        "time": shanghai_time,
                        "value": mmol_value
                    })

            # 分析运动数据
            activities = []
            for entry in activity_data:
                shanghai_time = entry.get("shanghai_time", "")
                if shanghai_time and len(shanghai_time) >= 16:
                    shanghai_time = shanghai_time[:16]
                
                activities.append({
                    "time": shanghai_time,
                    "event_type": entry.get("eventType", ""),
                    "duration": entry.get("duration", 0),
                    "notes": entry.get("notes", "")
                })

            prompt = f"""你是一位专业的内分泌科医生和糖尿病管理专家。请根据以下最近{days}天的血糖数据，回答用户的问题。{prompt_info} {treatment_prompt}

注意：所有时间显示均为用户本地时间（{timezone_name}），请基于此时区进行分析。

**重要要求：数据来源透明度**
- 必须明确区分实际测量的血糖数据和AI推理/预测的数值
- 对于任何非实际测量数据（如预测值、估算值、插值等），必须明确标注为"AI预测"、"估算值"或"推理数据"
- 例如：如果提到8.9 mmol/L这个数值，必须说明是实际测量还是AI推理得到
- 不得将AI推理数据混同为实际测量数据进行报告

血糖数据（mmol/L）:
"""
            for entry in glucose_mmol:
                prompt += f"• {entry['time']}: {entry['value']} mmol/L\n"

            if meter_mmol:
                prompt += f"\n指尖血糖数据（mmol/L）:\n"
                for entry in meter_mmol:
                    prompt += f"• {entry['time']}: {entry['value']} mmol/L\n"

            if activities:
                prompt += f"\n运动数据:\n"
                for activity in activities:
                    event_info = f"[{activity['event_type']}]" if activity['event_type'] else ""
                    notes_info = f" - {activity['notes']}" if activity['notes'] else ""
                    prompt += f"• {activity['time']}: {activity['duration']}分钟 {event_info}{notes_info}\n"

            prompt += f"""
用户问题: "{question}"

请用专业、简洁、易懂的语言回答，并提供可行的建议。如果数据不足以回答问题，请明确指出。

**重要提醒：**
- 所有提到的血糖数值必须明确区分是实际测量还是AI推理/预测
- 如果使用任何非实际测量的数值进行推测，必须明确标注数据来源
- 避免将AI推理数据呈现为实际测量结果
"""
        else:
            prompt = f"""你是一位专业的内分泌科医生和糖尿病管理专家。请回答以下用户的问题。{prompt_info} {treatment_prompt}

用户问题: "{question}"

请用专业、简洁、易懂的语言回答。
"""
        return prompt

    def send_web_notification(self, title: str, message: str):
        """发送Web推送通知"""
        try:
            # 通过SocketIO发送实时通知
            socketio.emit('notification', {
                'title': title,
                'message': message,
                'timestamp': datetime.now().isoformat()
            })
            logger.info(f"已发送Web通知: {title}")
        except Exception as e:
            logger.error(f"发送Web通知失败: {e}")

    def send_email_notification(self, subject: str, content: str, is_html: bool = False):
        """发送邮件通知"""
        start_time = time.time()
        
        try:
            if not self.config.get("notification", {}).get("enable_email", False):
                logger.info("邮件通知已禁用，跳过发送")
                return False

            email_config = self.config.get("email", {})
            if not all([
                email_config.get("smtp_server"),
                email_config.get("smtp_username"),
                email_config.get("smtp_password"),
                email_config.get("from_email"),
                email_config.get("to_emails")
            ]):
                logger.warning("邮件配置不完整，跳过邮件发送")
                return False

            # 验证邮箱格式
            from_email = email_config["from_email"]
            if not self.validate_email_format(from_email):
                logger.error(f"发件人邮箱格式不正确: {from_email}")
                return False

            to_emails = email_config["to_emails"]
            if isinstance(to_emails, list):
                for email in to_emails:
                    if isinstance(email, str):
                        email_clean = email.strip('"\'')
                        if not self.validate_email_format(email_clean):
                            logger.error(f"收件人邮箱格式不正确: {email_clean}")
                            return False
                    else:
                        logger.error(f"收件人邮箱格式错误: {email}")
                        return False
            else:
                logger.error("收件人邮箱配置格式错误")
                return False

            logger.info(f"开始发送邮件: {subject} 到 {len(to_emails)} 个收件人")

            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = email_config["from_email"]
            
            # 清理收件人邮箱地址（去除引号）
            cleaned_to_emails = [e.strip('"\'') if isinstance(e, str) else str(e) for e in to_emails]
            msg['To'] = ", ".join(cleaned_to_emails)
            msg['Date'] = formatdate(localtime=True)

            if is_html:
                # 如果内容已经是HTML格式
                html_content = content
                # 从HTML中提取纯文本
                import re
                text_content = re.sub('<[^<]+?>', '', content)
            else:
                # 创建HTML内容
                html_content = self.create_email_html_template(subject, content)
                text_content = content

            text_part = MIMEText(text_content, 'plain', 'utf-8')
            html_part = MIMEText(html_content, 'html', 'utf-8')

            msg.attach(text_part)
            msg.attach(html_part)

            try:
                # 发送邮件
                smtp_server = email_config["smtp_server"]
                smtp_port = email_config.get("smtp_port", 587)
                smtp_username = email_config["smtp_username"]
                smtp_password = email_config["smtp_password"]
                
                logger.info(f"连接SMTP服务器: {smtp_server}:{smtp_port}")
                logger.info(f"使用端口 {smtp_port}，判断连接类型：{'SMTP_SSL' if smtp_port == 465 else 'SMTP + STARTTLS'}")
                
                # 根据端口选择连接方式
                if smtp_port == 465:
                    # 端口465使用SSL连接
                    logger.info("使用SMTP_SSL连接（端口465）")
                    with smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=10) as server:
                        logger.info("SMTP_SSL连接成功，准备登录...")
                        server.login(smtp_username, smtp_password)
                        logger.info("登录成功，准备发送邮件...")
                        server.send_message(msg)
                        logger.info("邮件发送成功")
                else:
                    # 其他端口（如587）使用普通连接+STARTTLS
                    logger.info("使用SMTP + STARTTLS连接")
                    with smtplib.SMTP(smtp_server, smtp_port, timeout=10) as server:
                        logger.info("SMTP连接成功，准备启用STARTTLS...")
                        server.starttls()
                        logger.info("STARTTLS启用成功，准备登录...")
                        server.login(smtp_username, smtp_password)
                        logger.info("登录成功，准备发送邮件...")
                        server.send_message(msg)
                        logger.info("邮件发送成功")
                
                elapsed_time = time.time() - start_time
                logger.info(f"邮件发送成功: {subject} (耗时: {elapsed_time:.2f}秒)")
                return True
                
            finally:
                pass

        except smtplib.SMTPAuthenticationError as e:
            elapsed_time = time.time() - start_time
            logger.error(f"=== SMTP认证失败 ===")
            logger.error(f"错误详情: {e}")
            logger.error(f"服务器: {smtp_server}:{smtp_port}")
            logger.error(f"用户名: {smtp_username}")
            logger.error(f"耗时: {elapsed_time:.2f}秒")
            logger.error(f"可能原因: 用户名或密码错误，或者需要使用应用专用密码")
        except smtplib.SMTPConnectError as e:
            elapsed_time = time.time() - start_time
            logger.error(f"=== SMTP连接失败 ===")
            logger.error(f"错误详情: {e}")
            logger.error(f"服务器: {smtp_server}:{smtp_port}")
            logger.error(f"耗时: {elapsed_time:.2f}秒")
            logger.error(f"可能原因: 服务器地址错误、端口错误、网络问题或防火墙阻止")
        except smtplib.SMTPServerDisconnected as e:
            elapsed_time = time.time() - start_time
            logger.error(f"=== SMTP服务器连接断开 ===")
            logger.error(f"错误详情: {e}")
            logger.error(f"服务器: {smtp_server}:{smtp_port}")
            logger.error(f"耗时: {elapsed_time:.2f}秒")
            logger.error(f"可能原因: 服务器主动断开连接，可能是因为认证失败或协议错误")
        except smtplib.SMTPHeloError as e:
            elapsed_time = time.time() - start_time
            logger.error(f"=== SMTP HELO/EHLO命令失败 ===")
            logger.error(f"错误详情: {e}")
            logger.error(f"服务器: {smtp_server}:{smtp_port}")
            logger.error(f"耗时: {elapsed_time:.2f}秒")
            logger.error(f"可能原因: 服务器不支持HELO/EHLO命令或协议不兼容")
        except smtplib.SMTPRecipientsRefused as e:
            elapsed_time = time.time() - start_time
            logger.error(f"=== 收件人地址被拒绝 ===")
            logger.error(f"错误详情: {e}")
            logger.error(f"收件人: {cleaned_to_emails}")
            logger.error(f"耗时: {elapsed_time:.2f}秒")
            logger.error(f"可能原因: 收件人邮箱地址不存在或被服务器拒绝")
        except smtplib.SMTPSenderRefused as e:
            elapsed_time = time.time() - start_time
            logger.error(f"=== 发件人地址被拒绝 ===")
            logger.error(f"错误详情: {e}")
            logger.error(f"发件人: {from_email}")
            logger.error(f"耗时: {elapsed_time:.2f}秒")
            logger.error(f"可能原因: 发件人邮箱地址未验证或被服务器拒绝")
        except smtplib.SMTPDataError as e:
            elapsed_time = time.time() - start_time
            logger.error(f"=== 邮件数据格式错误 ===")
            logger.error(f"错误详情: {e}")
            logger.error(f"耗时: {elapsed_time:.2f}秒")
            logger.error(f"可能原因: 邮件内容格式不正确或包含被拒绝的内容")
        except socket.timeout as e:
            elapsed_time = time.time() - start_time
            logger.error(f"=== 邮件发送超时 ===")
            logger.error(f"错误详情: {e}")
            logger.error(f"服务器: {smtp_server}:{smtp_port}")
            logger.error(f"耗时: {elapsed_time:.2f}秒")
            logger.error(f"可能原因: 网络延迟或服务器响应慢")
        except ConnectionRefusedError as e:
            elapsed_time = time.time() - start_time
            logger.error(f"=== 连接被拒绝 ===")
            logger.error(f"错误详情: {e}")
            logger.error(f"服务器: {smtp_server}:{smtp_port}")
            logger.error(f"耗时: {elapsed_time:.2f}秒")
            logger.error(f"可能原因: 服务器未运行、端口未开放或被防火墙阻止")
        except Exception as e:
            elapsed_time = time.time() - start_time
            logger.error(f"=== 邮件发送失败 ===")
            logger.error(f"错误详情: {e}")
            logger.error(f"错误类型: {type(e).__name__}")
            logger.error(f"服务器: {smtp_server}:{smtp_port}")
            logger.error(f"耗时: {elapsed_time:.2f}秒")
            import traceback
            logger.error(f"详细错误堆栈:\n{traceback.format_exc()}")
        
        return False

    def send_xxtui_notification(self, title: str, content: str) -> bool:
        """通过XXTUI发送微信/短信通知"""
        start_time = time.time()
        
        try:
            if not self.config.get("alert", {}).get("enable_xxtui_alerts", False):
                logger.info("XXTUI通知已禁用，跳过发送")
                return False

            xxtui_config = self.config.get("xxtui", {})
            api_key = xxtui_config.get("api_key")
            from_name = xxtui_config.get("from", "Nightscout")
            
            if not api_key:
                logger.warning("XXTUI API Key未配置，跳过发送")
                return False

            # 构建请求数据
            payload = {
                "from": from_name,
                "title": title,
                "content": content
            }

            # 发送请求
            url = f"https://www.xxtui.com/xxtui/{api_key}"
            headers = {
                "Content-Type": "application/json"
            }

            async def send_request():
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=payload, headers=headers, timeout=30) as response:
                        if response.status == 200:
                            return True, await response.text()
                        else:
                            return False, f"HTTP {response.status}: {await response.text()}"

            # 运行异步请求
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            success, result = loop.run_until_complete(send_request())
            loop.close()

            if success:
                elapsed_time = time.time() - start_time
                logger.info(f"XXTUI通知发送成功: {title} (耗时: {elapsed_time:.2f}秒)")
                return True
            else:
                elapsed_time = time.time() - start_time
                logger.error(f"XXTUI通知发送失败: {result} (耗时: {elapsed_time:.2f}秒)")
                return False

        except Exception as e:
            elapsed_time = time.time() - start_time
            logger.error(f"XXTUI通知发送异常: {e} (耗时: {elapsed_time:.2f}秒)")
            return False

    def create_email_html_template(self, subject: str, content: str) -> str:
        """创建邮件HTML模板"""
        from datetime import datetime
        # 预格式化时间字符串，避免f-string中的方法调用
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # 将纯文本内容转换为HTML格式
        html_content = content.replace('\n', '<br>')

        # 添加一些基本的样式
        html_content = html_content.replace('📊', '<span style="color: #007bff;">📊</span>')
        html_content = html_content.replace('📈', '<span style="color: #28a745;">📈</span>')
        html_content = html_content.replace('🍽️', '<span style="color: #fd7e14;">🍽️</span>')
        html_content = html_content.replace('📋', '<span style="color: #6f42c1;">📋</span>')
        html_content = html_content.replace('✅', '<span style="color: #28a745;">✅</span>')
        html_content = html_content.replace('⚠️', '<span style="color: #ffc107;">⚠️</span>')
        html_content = html_content.replace('🚨', '<span style="color: #dc3545;">🚨</span>')

        return f"""
        <!DOCTYPE html>
        <html lang="zh-CN">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>{subject}</title>
            <style>
                body {{
                    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
                    line-height: 1.6;
                    color: #333;
                    max-width: 600px;
                    margin: 0 auto;
                    padding: 20px;
                    background-color: #f8f9fa;
                }}
                .container {{
                    background-color: white;
                    padding: 30px;
                    border-radius: 8px;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                }}
                .header {{
                    text-align: center;
                    border-bottom: 2px solid #007bff;
                    padding-bottom: 20px;
                    margin-bottom: 30px;
                }}
                .header h1 {{
                    color: #007bff;
                    margin: 0;
                    font-size: 24px;
                }}
                .content {{
                    font-size: 14px;
                    white-space: pre-line;
                    background-color: #f8f9fa;
                    padding: 20px;
                    border-radius: 5px;
                    border-left: 4px solid #007bff;
                }}
                .footer {{
                    margin-top: 30px;
                    padding-top: 20px;
                    border-top: 1px solid #dee2e6;
                    text-align: center;
                    color: #6c757d;
                    font-size: 12px;
                }}
                .timestamp {{
                    color: #6c757d;
                    font-size: 12px;
                    text-align: right;
                    margin-bottom: 20px;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🩺 糖小助</h1>
                    <p style="margin: 0; color: #6c757d;">{subject}</p>
                </div>
                <div class="timestamp">
                    生成时间: {current_time}
                </div>
                <div class="content">
                    {html_content}
                </div>
                <div class="footer">
                    <p>此邮件由糖小助自动发送</p>
                    <p>如有问题，请检查您的血糖监控设备和网络连接</p>
                </div>
            </div>
        </body>
        </html>
        """

    def validate_email_format(self, email: str) -> bool:
        """验证邮箱格式"""
        if not email or not isinstance(email, str):
            return False
        
        # 去除可能的引号
        email = email.strip('"\'')
        
        # 基本的邮箱格式验证
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return re.match(pattern, email) is not None

    def send_glucose_alert_notification(self, risk_assessment: Dict, alert_id: int = -1):
        """发送血糖报警通知（邮件和微信/短信）"""
        try:
            alert_config = self.get_user_alert_config()
            notification_sent = False
            
            # 构建报警内容
            subject = f"血糖报警通知 - {risk_assessment['risk_level']}风险"
            content = f"""
血糖预测报警详情：

预测血糖值: {risk_assessment['predicted_glucose_mmol']} mmol/L ({risk_assessment['predicted_glucose_mgdl']} mg/dL)
风险级别: {risk_assessment['risk_level']}
风险描述: {risk_assessment['risk_description']}
报警时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

请及时采取措施。

此通知由糖小助自动发送
"""
            
            # 发送邮件通知
            if alert_config.get('enable_email_alerts', False):
                if self.config.get("notification", {}).get("enable_email", False):
                    email_success = self.send_email_notification(subject, content)
                    if email_success:
                        logger.info(f"血糖报警邮件发送成功 - 风险级别: {risk_assessment['risk_level']}")
                        notification_sent = True
                    else:
                        logger.error(f"血糖报警邮件发送失败 - 风险级别: {risk_assessment['risk_level']}")
                else:
                    logger.info("邮件通知已禁用，跳过邮件发送")
            else:
                logger.info("血糖报警邮箱通知已禁用，跳过邮件发送")
            
            # 发送微信/短信通知
            if alert_config.get('enable_xxtui_alerts', False):
                xxtui_success = self.send_xxtui_notification(subject, content.strip())
                if xxtui_success:
                    logger.info(f"血糖报警微信/短信发送成功 - 风险级别: {risk_assessment['risk_level']}")
                    notification_sent = True
                else:
                    logger.error(f"血糖报警微信/短信发送失败 - 风险级别: {risk_assessment['risk_level']}")
            else:
                logger.info("血糖报警微信/短信通知已禁用，跳过发送")
            
            # 如果有任何通知发送成功，更新数据库状态
            if notification_sent and alert_id > 0:
                self._mark_alert_notification_sent(alert_id)
                    
            return notification_sent
                
        except Exception as e:
            logger.error(f"发送血糖报警通知失败: {e}")
            return False

    def _mark_alert_notification_sent(self, alert_id: int):
        """标记警报通知为已发送"""
        try:
            conn = sqlite3.connect(self.get_database_path())
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE hypoglycemia_alerts 
                SET notification_sent = 1, notification_time = ?
                WHERE id = ?
            """, (datetime.now().strftime('%Y-%m-%d %H:%M:%S'), alert_id))
            
            conn.commit()
            conn.close()
            
            logger.info(f"已标记警报 {alert_id} 通知为已发送")
            
        except Exception as e:
            logger.error(f"标记警报通知状态失败: {e}")

    def test_email_configuration(self) -> Dict[str, any]:
        """测试邮件配置"""
        try:
            email_config = self.config.get("email", {})

            # 检查配置完整性
            required_fields = ["smtp_server", "smtp_username", "smtp_password", "from_email", "to_emails"]
            missing_fields = [field for field in required_fields if not email_config.get(field)]

            if missing_fields:
                return {
                    "success": False,
                    "error": f"缺少必要配置: {', '.join(missing_fields)}"
                }

            # 验证邮箱格式
            from_email = email_config["from_email"]
            if not self.validate_email_format(from_email):
                return {
                    "success": False,
                    "error": "发件人邮箱格式不正确"
                }

            to_emails = email_config["to_emails"]
            if isinstance(to_emails, list):
                invalid_emails = []
                for email in to_emails:
                    if isinstance(email, str):
                        # 去除引号后验证
                        email_clean = email.strip('"\'')
                        if not self.validate_email_format(email_clean):
                            invalid_emails.append(email_clean)
                    else:
                        invalid_emails.append(str(email))
                
                if invalid_emails:
                    return {
                        "success": False,
                        "error": f"收件人邮箱格式不正确: {', '.join(invalid_emails)}"
                    }
            else:
                return {
                    "success": False,
                    "error": "收件人邮箱配置格式错误"
                }

            # 测试SMTP连接
            try:
                smtp_server = email_config["smtp_server"]
                smtp_port = email_config.get("smtp_port", 587)
                smtp_username = email_config["smtp_username"]
                smtp_password = email_config["smtp_password"]
                
                logger.info(f"测试SMTP连接: {smtp_server}:{smtp_port}")
                logger.info(f"使用端口 {smtp_port}，判断连接类型：{'SMTP_SSL' if smtp_port == 465 else 'SMTP + STARTTLS'}")
                
                # 根据端口选择连接方式
                if smtp_port == 465:
                    # 端口465使用SSL连接
                    logger.info("测试：使用SMTP_SSL连接（端口465）")
                    with smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=10) as server:
                        logger.info("测试：SMTP_SSL连接成功，准备登录...")
                        server.login(smtp_username, smtp_password)
                        logger.info("测试：登录成功")
                        
                        # 发送测试邮件
                        test_subject = "糖小助 - 邮件配置测试"
                        test_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        # 清理收件人邮箱地址，避免f-string中的转义字符
                        clean_to_emails = [e.strip('"\'') for e in email_config['to_emails']]
                        test_content = f"""
这是一封测试邮件，用于验证您的邮件配置是否正确。

📧 SMTP 服务器: {email_config['smtp_server']}:{email_config.get('smtp_port', 587)}
👤 发件人: {email_config['from_email']}
📮 收件人: {', '.join(clean_to_emails)}

如果您收到这封邮件，说明邮件配置已经成功！

测试时间: {test_time}
                        """
                        
                        # 创建邮件消息
                        msg = MIMEMultipart()
                        msg['From'] = email_config['from_email']
                        msg['To'] = ', '.join(clean_to_emails)
                        msg['Subject'] = test_subject
                        
                        # 添加邮件内容
                        msg.attach(MIMEText(test_content, 'plain', 'utf-8'))
                        
                        # 发送邮件
                        logger.info("测试：准备发送邮件...")
                        server.send_message(msg)
                        logger.info("测试：邮件发送成功")
                        
                        return {
                            "success": True,
                            "message": "邮件配置测试成功！测试邮件已发送"
                        }
                else:
                    # 其他端口（如587）使用普通连接+STARTTLS
                    logger.info("测试：使用SMTP + STARTTLS连接")
                    with smtplib.SMTP(smtp_server, smtp_port, timeout=10) as server:
                        logger.info("测试：SMTP连接成功，准备启用STARTTLS...")
                        server.starttls()
                        logger.info("测试：STARTTLS启用成功，准备登录...")
                        server.login(smtp_username, smtp_password)
                        logger.info("测试：登录成功")
                        
                        # 发送测试邮件
                        test_subject = "糖小助 - 邮件配置测试"
                        test_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        # 清理收件人邮箱地址，避免f-string中的转义字符
                        clean_to_emails = [e.strip('"\'') for e in email_config['to_emails']]
                        test_content = f"""
这是一封测试邮件，用于验证您的邮件配置是否正确。

📧 SMTP 服务器: {email_config['smtp_server']}:{email_config.get('smtp_port', 587)}
👤 发件人: {email_config['from_email']}
📮 收件人: {', '.join(clean_to_emails)}

如果您收到这封邮件，说明邮件配置已经成功！

测试时间: {test_time}
                        """
                        
                        # 创建邮件消息
                        msg = MIMEMultipart()
                        msg['From'] = email_config['from_email']
                        msg['To'] = ', '.join(clean_to_emails)
                        msg['Subject'] = test_subject
                        
                        # 添加邮件内容
                        msg.attach(MIMEText(test_content, 'plain', 'utf-8'))
                        
                        # 发送邮件
                        logger.info("测试：准备发送邮件...")
                        server.send_message(msg)
                        logger.info("测试：邮件发送成功")
                        
                        return {
                            "success": True,
                            "message": "邮件配置测试成功！测试邮件已发送"
                        }
            finally:
                pass

        except smtplib.SMTPAuthenticationError:
            return {
                "success": False,
                "error": "SMTP认证失败，请检查用户名和密码"
            }
        except smtplib.SMTPConnectError:
            return {
                "success": False,
                "error": "无法连接到SMTP服务器，请检查服务器地址和端口"
            }
        except smtplib.SMTPServerDisconnected:
            return {
                "success": False,
                "error": "SMTP服务器连接断开，请检查网络连接"
            }
        except smtplib.SMTPHeloError:
            return {
                "success": False,
                "error": "SMTP服务器不支持HELO/EHLO命令"
            }
        except smtplib.SMTPRecipientsRefused:
            return {
                "success": False,
                "error": "收件人地址被邮件服务器拒绝"
            }
        except smtplib.SMTPSenderRefused:
            return {
                "success": False,
                "error": "发件人地址被邮件服务器拒绝"
            }
        except smtplib.SMTPDataError:
            return {
                "success": False,
                "error": "邮件数据格式错误或被服务器拒绝"
            }
        except socket.timeout:
            return {
                "success": False,
                "error": "连接超时，请检查网络连接和邮件服务器状态"
            }
        except ConnectionRefusedError:
            return {
                "success": False,
                "error": "连接被拒绝，请检查SMTP服务器地址和端口"
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"邮件配置测试失败: {str(e)}"
            }

    def test_xxtui_configuration(self) -> Dict[str, any]:
        """测试XXTUI配置"""
        try:
            xxtui_config = self.config.get("xxtui", {})
            
            # 检查API Key
            api_key = xxtui_config.get("api_key")
            if not api_key:
                return {
                    "success": False,
                    "error": "XXTUI API Key未配置"
                }
            
            from_name = xxtui_config.get("from", "Nightscout")
            
            # 构建测试消息
            test_title = "糖小助 - XXTUI配置测试"
            test_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            test_content = f"""
这是一条测试消息，用于验证您的XXTUI配置是否正确。

📱 发送者: {from_name}
🔑 API Key: {api_key[:10]}...{api_key[-4:] if len(api_key) > 14 else api_key}
⏰ 测试时间: {test_time}

如果您收到这条消息，说明XXTUI配置已经成功！

此消息由糖小助自动发送
            """
            
            # 发送测试消息
            success = self.send_xxtui_notification(test_title, test_content.strip())
            
            if success:
                return {
                    "success": True,
                    "message": "XXTUI配置测试成功！测试消息已发送"
                }
            else:
                return {
                    "success": False,
                    "error": "XXTUI配置测试失败，请检查API Key是否正确"
                }
                
        except Exception as e:
            return {
                "success": False,
                "error": f"XXTUI配置测试失败: {str(e)}"
            }

    def calculate_bmi(self) -> Dict[str, any]:
        """计算BMI并返回状态"""
        height_cm = self.config.get("basic", {}).get("height_cm", 0)
        weight_kg = self.config.get("basic", {}).get("weight_kg", 0)

        if not height_cm or not weight_kg or height_cm <= 0 or weight_kg <= 0:
            return {"bmi": 0, "status": "信息不全"}

        height_m = height_cm / 100
        bmi = round(weight_kg / (height_m ** 2), 1)

        if bmi < 18.5:
            status = "偏瘦"
        elif 18.5 <= bmi < 24:
            status = "正常"
        elif 24 <= bmi < 28:
            status = "超重"
        else:
            status = "肥胖"
        
        return {"bmi": bmi, "status": status}

    def filter_data_by_exclude_times(self, data: List[Dict], exclude_times: List[Dict]) -> List[Dict]:
        """根据排除时间段过滤数据"""
        if not exclude_times or not data:
            return data
        
        # 将排除时间段转换为datetime对象（上海时间）
        exclude_ranges = []
        for exclude_time in exclude_times:
            try:
                start_time = datetime.strptime(exclude_time['start'], '%Y-%m-%d %H:%M')
                end_time = datetime.strptime(exclude_time['end'], '%Y-%m-%d %H:%M')
                exclude_ranges.append((start_time, end_time))
            except ValueError as e:
                logger.warning(f"解析排除时间段失败: {exclude_time}, 错误: {e}")
                continue
        
        if not exclude_ranges:
            return data
        
        filtered_data = []
        for item in data:
            # 获取数据项的时间戳
            item_time_str = None
            if 'shanghai_time' in item and item['shanghai_time']:
                item_time_str = item['shanghai_time']
            elif 'dateString' in item and item['dateString']:
                item_time_str = item['dateString']
            elif 'created_at' in item and item['created_at']:
                item_time_str = item['created_at']
            
            if not item_time_str:
                # 如果没有时间信息，保留该数据项
                filtered_data.append(item)
                continue
            
            try:
                # 解析时间戳为上海时间
                if item_time_str.endswith('Z'):
                    # UTC时间格式 - 转换为上海时间
                    if '.' in item_time_str:
                        utc_time = datetime.fromisoformat(item_time_str[:-1]).replace(tzinfo=timezone.utc)
                    else:
                        utc_time = datetime.fromisoformat(item_time_str[:-1]).replace(tzinfo=timezone.utc)
                    # 转换为上海时间（UTC+8）
                    shanghai_tz = timezone(timedelta(hours=8))
                    item_time = utc_time.astimezone(shanghai_tz)
                    # 移除时区信息以便比较
                    item_time = item_time.replace(tzinfo=None)
                else:
                    # 上海时间格式
                    item_time = datetime.strptime(item_time_str, '%Y-%m-%d %H:%M:%S')
                
                # 检查是否在任何排除时间段内
                is_excluded = False
                for exclude_start, exclude_end in exclude_ranges:
                    if exclude_start <= item_time <= exclude_end:
                        is_excluded = True
                        break
                
                if not is_excluded:
                    filtered_data.append(item)
                    
            except (ValueError, TypeError) as e:
                logger.warning(f"解析数据时间失败: {item_time_str}, 错误: {e}")
                # 如果时间解析失败，保留该数据项
                filtered_data.append(item)
        
        return filtered_data

    def generate_report_data(self, start_date: str, end_date: str, exclude_times: Optional[List[Dict]] = None) -> Dict:
        """生成血糖详细报告数据"""
        try:
            # 获取指定日期范围的数据
            glucose_data = self.get_glucose_data_from_db(start_date=start_date, end_date=end_date)
            treatment_data = self.get_treatment_data_from_db(start_date=start_date, end_date=end_date)
            activity_data = self.get_activity_data_from_db(start_date=start_date, end_date=end_date)
            meter_data = self.get_meter_data_from_db(start_date=start_date, end_date=end_date)
            
            # 应用排除时间段过滤 - 只排除CGM血糖数据，保留其他数据
            if exclude_times:
                glucose_data = self.filter_data_by_exclude_times(glucose_data, exclude_times)
                # 注意：不排除treatment_data（餐食数据）、activity_data（运动数据）和meter_data（指尖血糖数据）
            
            if not glucose_data:
                return {
                    'summary': {},
                    'daily_data': [],
                    'activity_data': [],
                    'meter_data': [],
                    'error': '暂无血糖数据'
                }

            # 转换血糖值为mmol/L
            glucose_values = []
            glucose_by_date = {}
            
            for entry in glucose_data:
                if entry.get("sgv"):
                    mmol_value = self.mg_dl_to_mmol_l(entry["sgv"])
                    glucose_values.append(mmol_value)
                    
                    # 按日期分组
                    date_str = entry.get('shanghai_time', '')[:10]
                    if date_str not in glucose_by_date:
                        glucose_by_date[date_str] = []
                    glucose_by_date[date_str].append({
                        'time': entry.get('shanghai_time', ''),
                        'value': mmol_value,
                        'hour': int(entry.get('shanghai_time', '00:00:00')[11:13]),
                        'timestamp': datetime.strptime(entry.get('shanghai_time', ''), '%Y-%m-%d %H:%M:%S') if entry.get('shanghai_time') else None
                    })

            # 处理指尖血糖数据（保持原有单位，确保数据一致性）
            meter_values = []
            meter_by_date = {}
            for entry in meter_data:
                if entry.get("sgv"):
                    # 确保指尖血糖数据以mmol/L单位处理
                    mmol_value = float(entry["sgv"])
                    meter_values.append(mmol_value)
                    
                    date_str = entry.get('shanghai_time', '')[:10]
                    if date_str not in meter_by_date:
                        meter_by_date[date_str] = []
                    meter_by_date[date_str].append({
                        'time': entry.get('shanghai_time', ''),
                        'value': mmol_value,
                        'hour': int(entry.get('shanghai_time', '00:00:00')[11:13]),
                        'timestamp': datetime.strptime(entry.get('shanghai_time', ''), '%Y-%m-%d %H:%M:%S') if entry.get('shanghai_time') else None
                    })

            # 处理餐食数据，用于优化餐后血糖计算
            meals_by_date = {}
            for entry in treatment_data:
                if entry.get("carbs") and entry.get("carbs") > 0:
                    date_str = entry.get('shanghai_time', '')[:10]
                    if date_str not in meals_by_date:
                        meals_by_date[date_str] = []
                    
                    hour = int(entry.get('shanghai_time', '00:00:00')[11:13])
                    meals_by_date[date_str].append({
                        'time': entry.get('shanghai_time', ''),
                        'carbs': entry.get("carbs"),
                        'hour': hour,
                        'timestamp': datetime.strptime(entry.get('shanghai_time', ''), '%Y-%m-%d %H:%M:%S') if entry.get('shanghai_time') else None
                    })

            # 处理运动数据
            activity_by_date = {}
            total_activity_duration = 0
            for entry in activity_data:
                date_str = entry.get('shanghai_time', '')[:10]
                if date_str not in activity_by_date:
                    activity_by_date[date_str] = []
                
                duration = entry.get('duration', 0)
                total_activity_duration += duration
                
                activity_by_date[date_str].append({
                    'time': entry.get('shanghai_time', ''),
                    'event_type': entry.get('eventType', ''),
                    'duration': duration,
                    'notes': entry.get('notes', '')
                })

            if not glucose_values:
                return {
                    'summary': {},
                    'daily_data': [],
                    'activity_data': activity_data,
                    'meter_data': meter_data,
                    'error': '血糖数据格式错误'
                }

            # 计算统计摘要
            avg_glucose = sum(glucose_values) / len(glucose_values)
            max_glucose = max(glucose_values)
            min_glucose = min(glucose_values)
            
            # 计算目标范围内比例
            in_range_count = sum(1 for v in glucose_values if 3.9 <= v <= 10.0)
            in_range_percentage = (in_range_count / len(glucose_values)) * 100
            
            # 计算糖化血红蛋白
            hba1c_data = self.calculate_estimated_hba1c(glucose_values)
            hba1c = hba1c_data.get("hba1c_adag_percent", 0)
            
            # 计算血糖变异系数
            cv_data = self.calculate_glucose_cv(glucose_values)
            cv = cv_data.get("cv_percent", 0)

            # 注意：指尖血糖数据不参与统计概览计算，仅在每日记录中显示

            # 计算空腹和餐后血糖
            fasting_values = []
            postprandial_values = []
            
            # 按日期处理数据
            daily_data = []
            date_range = []
            start_dt = datetime.strptime(start_date, '%Y-%m-%d')
            end_dt = datetime.strptime(end_date, '%Y-%m-%d')
            
            current_dt = start_dt
            while current_dt <= end_dt:
                date_str = current_dt.strftime('%Y-%m-%d')
                date_range.append(date_str)
                current_dt += timedelta(days=1)

            for date_str in date_range:
                day_data = {
                    'date': date_str,
                    'fasting': None,
                    'fasting_meter': None,
                    'breakfast_before': None,
                    'breakfast_before_meter': None,
                    'breakfast_after': None,
                    'breakfast_after_meter': None,
                    'lunch_before': None,
                    'lunch_before_meter': None,
                    'lunch_after': None,
                    'lunch_after_meter': None,
                    'dinner_before': None,
                    'dinner_before_meter': None,
                    'dinner_after': None,
                    'dinner_after_meter': None,
                    'activities': [],
                    'meter_readings': []
                }

                if date_str in glucose_by_date:
                    day_glucose = glucose_by_date[date_str]
                    
                    # 空腹血糖：早上6-7点
                    fasting_glucose = next((g['value'] for g in day_glucose if 6 <= g['hour'] < 7), None)
                    day_data['fasting'] = fasting_glucose
                    if fasting_glucose:
                        fasting_values.append(fasting_glucose)
                    
                    # 早餐前：早上6-8点
                    breakfast_before = next((g['value'] for g in day_glucose if 6 <= g['hour'] < 8), None)
                    day_data['breakfast_before'] = breakfast_before
                    
                    # 早餐后：基于实际餐食时间的2小时后或8-10点
                    breakfast_after = None
                    if date_str in meals_by_date:
                        # 查找当天的早餐记录（6-9点之间有碳水摄入的记录）
                        breakfast_meals = [m for m in meals_by_date[date_str] if 6 <= m['hour'] < 9]
                        if breakfast_meals:
                            # 取最早的早餐记录
                            breakfast_meal = min(breakfast_meals, key=lambda x: x['hour'])
                            if breakfast_meal['timestamp']:
                                # 计算餐后2小时的目标时间
                                target_time = breakfast_meal['timestamp'] + timedelta(hours=2)
                                target_hour = target_time.hour
                                
                                # 获取目标时间前后30分钟内的所有血糖数据
                                time_window_start = target_time - timedelta(minutes=30)
                                time_window_end = target_time + timedelta(minutes=30)
                                
                                # 从中选择最接近目标时间的血糖值作为餐后血糖
                                window_glucose = [
                                    g for g in day_glucose
                                    if g['timestamp'] and
                                       time_window_start <= g['timestamp'] <= time_window_end
                                ]
                                
                                if window_glucose:
                                    # 找到最接近目标时间的血糖值
                                    breakfast_after = min(
                                        window_glucose,
                                        key=lambda x: abs(x['timestamp'] - target_time)
                                    )['value']
                                else:
                                    # 如果没有找到，使用原来的逻辑（8-10点）
                                    breakfast_after = next((g['value'] for g in day_glucose if 8 <= g['hour'] < 10), None)
                    
                    # 如果没有找到基于餐食时间的餐后血糖，使用原来的逻辑
                    if breakfast_after is None:
                        breakfast_after = next((g['value'] for g in day_glucose if 8 <= g['hour'] < 10), None)
                    
                    if breakfast_after:
                        postprandial_values.append(breakfast_after)
                    day_data['breakfast_after'] = breakfast_after
                    
                    # 午餐前：11-12点
                    lunch_before = next((g['value'] for g in day_glucose if 11 <= g['hour'] < 12), None)
                    day_data['lunch_before'] = lunch_before
                    
                    # 午餐后：基于实际餐食时间计算
                    lunch_after = None
                    if date_str in meals_by_date:
                        # 查找当天的午餐记录（11-14点之间有碳水摄入的记录）
                        lunch_meals = [m for m in meals_by_date[date_str] if 11 <= m['hour'] < 14]
                        if lunch_meals:
                            # 取最早的午餐记录
                            lunch_meal = min(lunch_meals, key=lambda x: x['hour'])
                            if lunch_meal['timestamp']:
                                # 计算餐后2小时的目标时间
                                target_time = lunch_meal['timestamp'] + timedelta(hours=2)
                                target_hour = target_time.hour
                                
                                # 获取目标时间前后30分钟内的所有血糖数据
                                time_window_start = target_time - timedelta(minutes=30)
                                time_window_end = target_time + timedelta(minutes=30)
                                
                                # 从中选择最接近目标时间的血糖值作为餐后血糖
                                window_glucose = [
                                    g for g in day_glucose
                                    if g['timestamp'] and
                                       time_window_start <= g['timestamp'] <= time_window_end
                                ]
                                
                                if window_glucose:
                                    # 找到最接近目标时间的血糖值
                                    lunch_after = min(
                                        window_glucose,
                                        key=lambda x: abs(x['timestamp'] - target_time)
                                    )['value']
                                else:
                                    # 如果没有找到，使用原来的逻辑（12-14点）
                                    lunch_after = next((g['value'] for g in day_glucose if 12 <= g['hour'] < 14), None)
                    
                    # 如果没有找到基于餐食时间的餐后血糖，使用原来的逻辑
                    if lunch_after is None:
                        lunch_after = next((g['value'] for g in day_glucose if 12 <= g['hour'] < 14), None)
                    
                    if lunch_after:
                        postprandial_values.append(lunch_after)
                    day_data['lunch_after'] = lunch_after
                    
                    # 晚餐前：17-18点
                    dinner_before = next((g['value'] for g in day_glucose if 17 <= g['hour'] < 18), None)
                    day_data['dinner_before'] = dinner_before
                    
                    # 晚餐后：基于实际餐食时间的2小时后或18-20点
                    dinner_after = None
                    if date_str in meals_by_date:
                        # 查找当天的晚餐记录（17-19点之间有碳水摄入的记录）
                        dinner_meals = [m for m in meals_by_date[date_str] if 17 <= m['hour'] < 19]
                        if dinner_meals:
                            # 取最早的晚餐记录
                            dinner_meal = min(dinner_meals, key=lambda x: x['hour'])
                            if dinner_meal['timestamp']:
                                # 计算餐后2小时的目标时间
                                target_time = dinner_meal['timestamp'] + timedelta(hours=2)
                                target_hour = target_time.hour
                                
                                # 获取目标时间前后30分钟内的所有血糖数据
                                time_window_start = target_time - timedelta(minutes=30)
                                time_window_end = target_time + timedelta(minutes=30)
                                
                                # 从中选择最接近目标时间的血糖值作为餐后血糖
                                window_glucose = [
                                    g for g in day_glucose
                                    if g['timestamp'] and
                                       time_window_start <= g['timestamp'] <= time_window_end
                                ]
                                
                                if window_glucose:
                                    # 找到最接近目标时间的血糖值
                                    dinner_after = min(
                                        window_glucose,
                                        key=lambda x: abs(x['timestamp'] - target_time)
                                    )['value']
                                else:
                                    # 如果没有找到，使用原来的逻辑（18-20点）
                                    dinner_after = next((g['value'] for g in day_glucose if 18 <= g['hour'] < 20), None)
                    
                    # 如果没有找到基于餐食时间的餐后血糖，使用原来的逻辑
                    if dinner_after is None:
                        dinner_after = next((g['value'] for g in day_glucose if 18 <= g['hour'] < 20), None)
                    
                    if dinner_after:
                        postprandial_values.append(dinner_after)
                    day_data['dinner_after'] = dinner_after

                # 查找对应时间段的指尖血糖数据
                if date_str in meter_by_date:
                    day_meter = meter_by_date[date_str]
                    
                    # 空腹血糖对应的指尖血糖（6-7点）
                    fasting_meter = next((m['value'] for m in day_meter if 6 <= m['hour'] < 7), None)
                    day_data['fasting_meter'] = fasting_meter
                    
                    # 早餐前对应的指尖血糖（6-8点）
                    breakfast_before_meter = next((m['value'] for m in day_meter if 6 <= m['hour'] < 8), None)
                    day_data['breakfast_before_meter'] = breakfast_before_meter
                    
                    # 早餐后对应的指尖血糖（基于实际餐食时间或8-10点）
                    breakfast_after_meter = None
                    if date_str in meals_by_date:
                        # 查找当天的早餐记录（6-9点之间有碳水摄入的记录）
                        breakfast_meals = [m for m in meals_by_date[date_str] if 6 <= m['hour'] < 9]
                        if breakfast_meals:
                            # 取最早的早餐记录
                            breakfast_meal = min(breakfast_meals, key=lambda x: x['hour'])
                            if breakfast_meal['timestamp']:
                                # 计算餐后2小时的目标时间
                                target_time = breakfast_meal['timestamp'] + timedelta(hours=2)
                                target_hour = target_time.hour
                                
                                # 获取目标时间前后30分钟内的所有指尖血糖数据
                                time_window_start = target_time - timedelta(minutes=30)
                                time_window_end = target_time + timedelta(minutes=30)
                                
                                # 从中选择最接近目标时间的指尖血糖值
                                window_meter = [
                                    m for m in day_meter
                                    if m['timestamp'] and
                                       time_window_start <= m['timestamp'] <= time_window_end
                                ]
                                
                                if window_meter:
                                    # 找到最接近目标时间的指尖血糖值
                                    breakfast_after_meter = min(
                                        window_meter,
                                        key=lambda x: abs(x['timestamp'] - target_time)
                                    )['value']
                    
                    # 如果没有找到基于餐食时间的餐后指尖血糖，使用原来的逻辑（8-10点）
                    if breakfast_after_meter is None:
                        breakfast_after_meter = next((m['value'] for m in day_meter if 8 <= m['hour'] < 10), None)
                    
                    day_data['breakfast_after_meter'] = breakfast_after_meter
                    
                    # 午餐前对应的指尖血糖（11-12点）
                    lunch_before_meter = next((m['value'] for m in day_meter if 11 <= m['hour'] < 12), None)
                    day_data['lunch_before_meter'] = lunch_before_meter
                    
                    # 午餐后对应的指尖血糖（基于实际餐食时间或12-14点）
                    lunch_after_meter = None
                    if date_str in meals_by_date:
                        # 查找当天的午餐记录（11-14点之间有碳水摄入的记录）
                        lunch_meals = [m for m in meals_by_date[date_str] if 11 <= m['hour'] < 14]
                        if lunch_meals:
                            # 取最早的午餐记录
                            lunch_meal = min(lunch_meals, key=lambda x: x['hour'])
                            if lunch_meal['timestamp']:
                                # 计算餐后2小时的目标时间
                                target_time = lunch_meal['timestamp'] + timedelta(hours=2)
                                target_hour = target_time.hour
                                
                                # 获取目标时间前后30分钟内的所有指尖血糖数据
                                time_window_start = target_time - timedelta(minutes=30)
                                time_window_end = target_time + timedelta(minutes=30)
                                
                                # 从中选择最接近目标时间的指尖血糖值
                                window_meter = [
                                    m for m in day_meter
                                    if m['timestamp'] and
                                       time_window_start <= m['timestamp'] <= time_window_end
                                ]
                                
                                if window_meter:
                                    # 找到最接近目标时间的指尖血糖值
                                    lunch_after_meter = min(
                                        window_meter,
                                        key=lambda x: abs(x['timestamp'] - target_time)
                                    )['value']
                    
                    # 如果没有找到基于餐食时间的餐后指尖血糖，使用原来的逻辑（12-14点）
                    if lunch_after_meter is None:
                        lunch_after_meter = next((m['value'] for m in day_meter if 12 <= m['hour'] < 14), None)
                    
                    day_data['lunch_after_meter'] = lunch_after_meter
                    
                    # 晚餐前对应的指尖血糖（17-18点）
                    dinner_before_meter = next((m['value'] for m in day_meter if 17 <= m['hour'] < 18), None)
                    day_data['dinner_before_meter'] = dinner_before_meter
                    
                    # 晚餐后对应的指尖血糖（基于实际餐食时间或18-20点）
                    dinner_after_meter = None
                    if date_str in meals_by_date:
                        # 查找当天的晚餐记录（17-19点之间有碳水摄入的记录）
                        dinner_meals = [m for m in meals_by_date[date_str] if 17 <= m['hour'] < 19]
                        if dinner_meals:
                            # 取最早的晚餐记录
                            dinner_meal = min(dinner_meals, key=lambda x: x['hour'])
                            if dinner_meal['timestamp']:
                                # 计算餐后2小时的目标时间
                                target_time = dinner_meal['timestamp'] + timedelta(hours=2)
                                target_hour = target_time.hour
                                
                                # 获取目标时间前后30分钟内的所有指尖血糖数据
                                time_window_start = target_time - timedelta(minutes=30)
                                time_window_end = target_time + timedelta(minutes=30)
                                
                                # 从中选择最接近目标时间的指尖血糖值
                                window_meter = [
                                    m for m in day_meter
                                    if m['timestamp'] and
                                       time_window_start <= m['timestamp'] <= time_window_end
                                ]
                                
                                if window_meter:
                                    # 找到最接近目标时间的指尖血糖值
                                    dinner_after_meter = min(
                                        window_meter,
                                        key=lambda x: abs(x['timestamp'] - target_time)
                                    )['value']
                    
                    # 如果没有找到基于餐食时间的餐后指尖血糖，使用原来的逻辑（18-20点）
                    if dinner_after_meter is None:
                        dinner_after_meter = next((m['value'] for m in day_meter if 18 <= m['hour'] < 20), None)
                    
                    day_data['dinner_after_meter'] = dinner_after_meter

                # 添加当天的运动数据
                if date_str in activity_by_date:
                    day_data['activities'] = activity_by_date[date_str]

                # 添加当天的指尖血糖数据
                if date_str in meter_by_date:
                    day_data['meter_readings'] = meter_by_date[date_str]

                daily_data.append(day_data)

            # 计算空腹和餐后平均血糖
            fasting_avg = sum(fasting_values) / len(fasting_values) if fasting_values else 0
            postprandial_avg = sum(postprandial_values) / len(postprandial_values) if postprandial_values else 0

            return {
                'summary': {
                    'avg_glucose': round(avg_glucose, 1),
                    'max_glucose': round(max_glucose, 1),
                    'min_glucose': round(min_glucose, 1),
                    'hba1c': round(hba1c, 1),
                    'cv': round(cv, 1),
                    'in_range_percentage': round(in_range_percentage, 1),
                    'fasting_avg': round(fasting_avg, 1),
                    'postprandial_avg': round(postprandial_avg, 1),
                    'total_activity_duration': total_activity_duration,
                    'activity_count': len(activity_data)
                },
                'daily_data': daily_data,
                'activity_data': activity_data,
                'meter_data': meter_data
            }

        except Exception as e:
            logger.error(f"生成报表数据失败: {e}")
            return {
                'summary': {},
                'daily_data': [],
                'activity_data': [],
                'meter_data': [],
                'error': str(e)
            }

    def predict_glucose(self, glucose_data: List[Dict], treatment_data: List[Dict] = None, force_current_based: bool = False) -> Dict:
        """预测血糖值 - 增强版算法，整合数据质量评分、动态权重和改进置信度模型，支持饮食和运动数据增强"""
        try:
            # 1. 数据质量评估
            quality_assessment = self.calculate_data_quality_score(glucose_data)
            
            # 基于质量等级调整数据要求
            min_data_points = self._get_min_data_points_based_on_quality(quality_assessment['quality_level'])
            if len(glucose_data) < min_data_points:
                quality_msg = quality_assessment.get('warning_message', '数据质量不足')
                raise ValueError(f"{quality_msg}，至少需要{min_data_points}个数据点")
            
            # 验证数据时间范围（1-7天）
            if glucose_data:
                sorted_data = sorted(glucose_data, key=lambda x: x.get('shanghai_time', ''))
                if sorted_data:
                    earliest_time = sorted_data[0].get('shanghai_time', '')
                    latest_time = sorted_data[-1].get('shanghai_time', '')
                    
                    if earliest_time and latest_time:
                        try:
                            from datetime import datetime
                            earliest_dt = datetime.strptime(earliest_time, '%Y-%m-%d %H:%M:%S')
                            latest_dt = datetime.strptime(latest_time, '%Y-%m-%d %H:%M:%S')
                            time_diff = latest_dt - earliest_dt
                            days_diff = time_diff.total_seconds() / (24 * 3600)
                            
                            if days_diff < 1:
                                raise ValueError(f"数据时间范围不足，至少需要1天数据（当前：{days_diff:.1f}天）")
                            if days_diff > 7:
                                raise ValueError(f"数据时间范围过长，最多支持7天数据（当前：{days_diff:.1f}天）")
                        except ValueError as ve:
                            if "time data" in str(ve):
                                logger.warning(f"时间格式解析失败，跳过时间范围验证: {ve}")
                            else:
                                raise ve
            
            # 按时间排序（从旧到新）
            sorted_data = sorted(glucose_data, key=lambda x: x.get('shanghai_time', ''))
            
            # 2. 数据预处理和异常值过滤
            cleaned_data = self._preprocess_glucose_data(sorted_data, quality_assessment)
            
            # 获取当前血糖值（最新的血糖值）
            current_glucose_mgdl = None
            for entry in reversed(cleaned_data):
                sgv = entry.get('sgv', 0)
                if sgv > 0:
                    current_glucose_mgdl = sgv
                    break
            
            if current_glucose_mgdl is None:
                raise ValueError("无法获取当前血糖值")
            
            # 获取最近的血糖值用于趋势计算（最近15个数据点，比原来更多）
            recent_glucose_values = []
            data_quality_scores = []
            
            for entry in cleaned_data[-15:]:
                sgv = entry.get('sgv', 0)
                if sgv > 0:
                    recent_glucose_values.append(sgv)
                    # 计算每个数据点的质量分数
                    data_quality_scores.append(self._calculate_individual_data_quality(entry, cleaned_data))
            
            if len(recent_glucose_values) < 5:
                raise ValueError("有效的血糖数据点不足")
            
            # 3. 饮食和运动数据整合
            lifestyle_factors = self._process_lifestyle_data(treatment_data, cleaned_data)
            
            # 4. 基于数据质量、新鲜度和生活方式因素的动态权重趋势计算
            trend_calculation = self._calculate_enhanced_trend(
                recent_glucose_values, data_quality_scores, quality_assessment, lifestyle_factors
            )
            
            avg_change = trend_calculation['avg_change']
            trend_weights = trend_calculation['weights']
            trend_confidence = trend_calculation['trend_confidence']
            
            # 4. 生成未来30分钟内的预测点（仅10、20、30分钟三个点）
            prediction_points = []
            
            # 如果强制使用当前值为基础，调整预测算法
            if force_current_based:
                logger.info("强制使用当前血糖值为基础进行预测")
                # 使用更保守的预测策略，主要基于当前值
                base_change_rate = self._calculate_conservative_change_rate(recent_glucose_values[-5:], current_glucose_mgdl)
                avg_change = base_change_rate['conservative_change']
                trend_confidence = base_change_rate['conservative_confidence']
            
            # 生成10、20、30分钟三个预测点
            current_time_factor = time.time() % 60 / 60.0  # 0-1之间的小时间扰动
            for minutes in [10, 20, 30]:
                # 使用动态权重和趋势置信度调整预测，考虑生活方式因素
                # 添加基于当前时间的小扰动，确保重新预测有轻微差异
                time_noise = (current_time_factor - 0.5) * 0.2  # ±0.1的扰动
                time_factor = (minutes / 5.0) * (1 + time_noise)  # 相对于5分钟的倍数
                lifestyle_adjustment = self._calculate_lifestyle_adjustment(lifestyle_factors, minutes)
                
                projected_change = avg_change * time_factor * (0.7 + 0.3 * trend_confidence) + lifestyle_adjustment * 0.8
                predicted_value = current_glucose_mgdl + projected_change
                prediction_points.append({
                    'minutes_ahead': minutes,
                    'predicted_glucose_mgdl': round(predicted_value, 1),
                    'predicted_glucose_mmol': round(predicted_value / 18.0, 1),
                    'confidence_adjustment': trend_confidence,
                    'lifestyle_adjustment': round(lifestyle_adjustment, 2)
                })
            
            predicted_glucose_mgdl = prediction_points[-1]['predicted_glucose_mgdl']
            
            # 5. 改进的置信度计算模型
            enhanced_confidence = self._calculate_enhanced_confidence(
                recent_glucose_values,
                data_quality_scores,
                quality_assessment,
                trend_confidence,
                current_glucose_mgdl,
                glucose_data
            )
            
            # 6. 单位转换
            predicted_glucose_mmol = round(predicted_glucose_mgdl / 18.0, 1)
            current_glucose_mmol = round(current_glucose_mgdl / 18.0, 1)
            
            prediction_result = {
                'predicted_glucose_mgdl': round(predicted_glucose_mgdl, 1),
                'predicted_glucose_mmol': predicted_glucose_mmol,
                'current_glucose_mgdl': current_glucose_mgdl,
                'current_glucose_mmol': current_glucose_mmol,
                'confidence_score': enhanced_confidence['final_score'],
                'trend_rate': round(avg_change, 2),
                'algorithm_used': 'enhanced_dynamic_weight_trend_extrapolation',
                'data_points_count': len(recent_glucose_values),
                'prediction_time': self._now_in_config_timezone().strftime('%Y-%m-%d %H:%M:%S'),
                'prediction_points': prediction_points,
                'quality_assessment': quality_assessment,
                'confidence_breakdown': enhanced_confidence,
                'trend_weights': trend_weights
            }
            
            # 7. 预测结果校验
            validation_result = self.validate_prediction_result(prediction_result, glucose_data)
            prediction_result['validation_result'] = validation_result
            
            # 如果校验失败，调整预测结果
            if not validation_result['is_valid']:
                logger.warning(f"预测结果校验失败: {validation_result['warnings']}")
                # 基于校验结果调整置信度
                prediction_result['confidence_score'] *= 0.7
                prediction_result['validation_warnings'] = validation_result['warnings']
            
            # 如果校验失败且不是强制模式，尝试自动重新预测
            if not validation_result['is_valid'] and not force_current_based:
                logger.warning(f"预测结果校验失败，尝试自动重新预测: {validation_result['warnings']}")
                
                # 检查是否需要强制重新预测（趋势严重不一致或生理不合理）
                severe_validation_failure = (
                    'trend_inconsistency' in validation_result['validation_flags'] or
                    'physiological_implausibility' in validation_result['validation_flags']
                )
                
                if severe_validation_failure:
                    logger.info("检测到严重预测错误，自动使用当前值重新预测")
                    try:
                        # 使用当前值为基础重新预测
                        return self.predict_glucose(glucose_data, treatment_data, force_current_based=True)
                    except Exception as retry_e:
                        logger.error(f"自动重新预测失败: {retry_e}")
                        # 继续返回原始预测结果
            
            prediction_result['validation_result'] = validation_result
            
            # 如果校验失败，调整预测结果
            if not validation_result['is_valid']:
                logger.warning(f"预测结果校验失败: {validation_result['warnings']}")
                # 基于校验结果调整置信度
                prediction_result['confidence_score'] *= 0.7
                prediction_result['validation_warnings'] = validation_result['warnings']
            
            return prediction_result
            
        except Exception as e:
            logger.error(f"增强版血糖预测失败: {e}")
            raise e

    def _calculate_conservative_change_rate(self, recent_values: List[float], current_glucose: float) -> Dict:
        """计算保守的变化率，主要用于强制基于当前值预测的情况"""
        try:
            if len(recent_values) < 2:
                return {
                    'conservative_change': 0.0,
                    'conservative_confidence': 0.5
                }
            
            # 计算短期的平均变化（最近几个点）
            if len(recent_values) >= 3:
                # 使用最后3个点的变化趋势
                recent_changes = []
                for i in range(1, min(4, len(recent_values))):
                    change = recent_values[-i] - recent_values[-i-1]
                    recent_changes.append(change)
                
                if recent_changes:
                    # 使用中位数而不是平均值，避免极端值影响
                    recent_changes.sort()
                    if len(recent_changes) % 2 == 1:
                        median_change = recent_changes[len(recent_changes) // 2]
                    else:
                        median_change = (recent_changes[len(recent_changes) // 2 - 1] + recent_changes[len(recent_values) // 2]) / 2
                    
                    # 应用保守调整因子
                    conservative_change = median_change * 0.6  # 保守因子
                else:
                    conservative_change = 0.0
            else:
                # 数据点很少时，使用最小变化
                conservative_change = (recent_values[-1] - recent_values[-2]) * 0.3
            
            # 基于当前血糖值调整变化率
            if current_glucose > 180:
                # 高血糖时，可能下降趋势更强
                conservative_change = min(conservative_change, -abs(conservative_change) * 0.5)
            elif current_glucose < 80:
                # 低血糖时，可能上升趋势更强
                conservative_change = max(conservative_change, abs(conservative_change) * 0.5)
            
            # 限制最大变化率，确保生理合理性
            max_change = min(abs(current_glucose) * 0.1, 15.0)  # 最多10%变化或15mg/dL
            conservative_change = max(-max_change, min(max_change, conservative_change))
            
            # 计算保守置信度
            conservative_confidence = 0.7  # 保守预测的基础置信度
            
            # 如果数据很新，增加置信度
            if len(recent_values) >= 5:
                conservative_confidence += 0.1
            
            # 如果变化很小，增加置信度
            if abs(conservative_change) < 3:
                conservative_confidence += 0.1
            
            conservative_confidence = min(conservative_confidence, 0.9)
            
            return {
                'conservative_change': conservative_change,
                'conservative_confidence': conservative_confidence
            }
            
        except Exception as e:
            logger.error(f"保守变化率计算失败: {e}")
            return {
                'conservative_change': 0.0,
                'conservative_confidence': 0.5
            }
    
    def calculate_data_quality_score(self, glucose_data: List[Dict]) -> Dict:
        """计算数据质量评分 - 基于及时性和一致性"""
        try:
            if not glucose_data or len(glucose_data) < 5:
                return {
                    'overall_score': 0.0,
                    'timeliness_score': 0.0,
                    'consistency_score': 0.0,
                    'quality_level': 'CRITICAL',
                    'warning_message': '数据点不足，无法进行质量评估'
                }
            
            # 按时间排序
            sorted_data = sorted(glucose_data, key=lambda x: x.get('shanghai_time', ''))
            
            # 1. 及时性评分 (Timeliness Score)
            timeliness_score = self._calculate_timeliness_score(sorted_data)
            
            # 2. 一致性评分 (Consistency Score)
            consistency_score = self._calculate_consistency_score(sorted_data)
            
            # 3. 综合评分
            overall_score = (timeliness_score * 0.4 + consistency_score * 0.6)
            
            # 4. 质量等级和阈值
            quality_level, warning_message = self._determine_quality_level(overall_score)
            
            return {
                'overall_score': round(overall_score, 2),
                'timeliness_score': round(timeliness_score, 2),
                'consistency_score': round(consistency_score, 2),
                'quality_level': quality_level,
                'warning_message': warning_message,
                'data_points_count': len(sorted_data)
            }
            
        except Exception as e:
            logger.error(f"计算数据质量评分失败: {e}")
            return {
                'overall_score': 0.0,
                'timeliness_score': 0.0,
                'consistency_score': 0.0,
                'quality_level': 'CRITICAL',
                'warning_message': f'质量评估失败: {str(e)}'
            }
    
    def _calculate_timeliness_score(self, sorted_data: List[Dict]) -> float:
        """计算及时性评分"""
        try:
            if not sorted_data:
                return 0.0
             
            current_time = self._now_in_config_timezone()
            time_gaps = []
            
            for i in range(len(sorted_data) - 1):
                current_time_str = sorted_data[i].get('shanghai_time', '')
                next_time_str = sorted_data[i + 1].get('shanghai_time', '')
                
                if current_time_str and next_time_str:
                    try:
                        current_dt = datetime.strptime(current_time_str, '%Y-%m-%d %H:%M:%S')
                        next_dt = datetime.strptime(next_time_str, '%Y-%m-%d %H:%M:%S')
                        gap_minutes = (next_dt - current_dt).total_seconds() / 60
                        time_gaps.append(gap_minutes)
                    except ValueError:
                        continue
            
            if not time_gaps:
                return 0.0
            
            # 理想时间间隔是5分钟
            ideal_gap = 5.0
            avg_gap = sum(time_gaps) / len(time_gaps)
            
            # 计算及时性分数 (0-100)
            # 时间间隔越接近5分钟，分数越高
            gap_ratio = min(ideal_gap / avg_gap, avg_gap / ideal_gap) if avg_gap > 0 else 0
            timeliness_score = max(0, min(100, gap_ratio * 100))
            
            # 考虑数据新鲜度
            latest_time_str = sorted_data[-1].get('shanghai_time', '')
            if latest_time_str:
                try:
                    latest_dt = datetime.strptime(latest_time_str, '%Y-%m-%d %H:%M:%S')
                    freshness_minutes = (current_time - latest_dt).total_seconds() / 60
                    
                    # 数据超过30分钟开始扣分
                    if freshness_minutes > 30:
                        freshness_penalty = min(50, (freshness_minutes - 30) / 30 * 10)
                        timeliness_score = max(0, timeliness_score - freshness_penalty)
                except ValueError:
                    timeliness_score *= 0.8
            
            return timeliness_score
            
        except Exception as e:
            logger.error(f"计算及时性评分失败: {e}")
            return 0.0
    
    def _calculate_consistency_score(self, sorted_data: List[Dict]) -> float:
        """计算一致性评分"""
        try:
            if not sorted_data:
                return 0.0
            
            glucose_values = []
            for entry in sorted_data:
                sgv = entry.get('sgv', 0)
                if sgv > 0:
                    glucose_values.append(sgv)
            
            if len(glucose_values) < 3:
                return 0.0
            
            # 1. 计算变化率的一致性
            changes = []
            for i in range(1, len(glucose_values)):
                change = abs(glucose_values[i] - glucose_values[i-1])
                changes.append(change)
            
            if not changes:
                return 50.0
            
            avg_change = sum(changes) / len(changes)
            
            # 2. 检测异常变化
            # 血糖变化率一般不会超过10 mg/dL每5分钟
            max_reasonable_change = 10.0
            anomalies = sum(1 for change in changes if change > max_reasonable_change)
            anomaly_ratio = anomalies / len(changes)
            
            # 3. 计算变化的一致性（标准差）
            if len(changes) > 1:
                variance = sum((x - avg_change) ** 2 for x in changes) / len(changes)
                std_dev = variance ** 0.5
                coefficient_of_variation = std_dev / avg_change if avg_change > 0 else 0
                
                # 变异系数越小，一致性越高
                consistency_factor = max(0, 1 - coefficient_of_variation)
            else:
                consistency_factor = 1.0
            
            # 4. 综合一致性评分
            anomaly_penalty = anomaly_ratio * 50  # 最多扣50分
            consistency_score = max(0, consistency_factor * 100 - anomaly_penalty)
            
            return consistency_score
            
        except Exception as e:
            logger.error(f"计算一致性评分失败: {e}")
            return 0.0
    
    def _determine_quality_level(self, overall_score: float) -> tuple:
        """确定质量等级和警告信息"""
        if overall_score >= 85:
            return 'EXCELLENT', '数据质量优秀'
        elif overall_score >= 70:
            return 'GOOD', '数据质量良好'
        elif overall_score >= 50:
            return 'FAIR', '数据质量一般，需要关注'
        elif overall_score >= 30:
            return 'POOR', '数据质量较差，建议检查数据源'
        else:
            return 'CRITICAL', '数据质量严重不足，预测可能不准确'

    def save_prediction_result(self, prediction_data: Dict) -> bool:
        """保存预测结果到数据库"""
        try:
            conn = sqlite3.connect(self.get_database_path())
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO prediction_results 
                (prediction_time, predicted_glucose_mgdl, predicted_glucose_mmol, 
                 confidence_score, algorithm_used, data_points_count)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                prediction_data['prediction_time'],
                prediction_data['predicted_glucose_mgdl'],
                prediction_data['predicted_glucose_mmol'],
                prediction_data['confidence_score'],
                prediction_data['algorithm_used'],
                prediction_data['data_points_count']
            ))
            
            conn.commit()
            conn.close()
            return True
            
        except Exception as e:
            logger.error(f"保存预测结果失败: {e}")
            return False

    def assess_hypoglycemia_risk(self, predicted_glucose_mgdl: float) -> Dict:
        """评估低血糖风险"""
        try:
            # 获取用户配置
            config = self.get_user_alert_config()
            high_threshold = config.get('high_risk_threshold_mgdl', 70)
            medium_threshold = config.get('medium_risk_threshold_mgdl', 80)
            
            predicted_glucose_mmol = round(predicted_glucose_mgdl / 18.0, 1)
            
            # 风险评估逻辑
            if predicted_glucose_mgdl < high_threshold:
                risk_level = 'HIGH'
                risk_description = '高风险：可能在30分钟内发生低血糖'
            elif predicted_glucose_mgdl < medium_threshold:
                risk_level = 'MEDIUM'
                risk_description = '中等风险：血糖可能偏低'
            else:
                risk_level = 'LOW'
                risk_description = '低风险：血糖水平正常'
            
            # 计算风险严重程度（0-100）
            if risk_level == 'HIGH':
                risk_severity = max(0, min(100, (high_threshold - predicted_glucose_mgdl) / high_threshold * 100))
            elif risk_level == 'MEDIUM':
                risk_severity = max(0, min(100, (medium_threshold - predicted_glucose_mgdl) / (medium_threshold - high_threshold) * 50))
            else:
                risk_severity = 0
            
            return {
                'risk_level': risk_level,
                'risk_description': risk_description,
                'risk_severity': round(risk_severity, 1),
                'predicted_glucose_mgdl': predicted_glucose_mgdl,
                'predicted_glucose_mmol': predicted_glucose_mmol,
                'thresholds': {
                    'high_risk_mgdl': high_threshold,
                    'medium_risk_mgdl': medium_threshold,
                    'high_risk_mmol': round(high_threshold / 18.0, 1),
                    'medium_risk_mmol': round(medium_threshold / 18.0, 1)
                }
            }
            
        except Exception as e:
            logger.error(f"低血糖风险评估失败: {e}")
            raise e

    def create_hypoglycemia_alert(self, risk_assessment: Dict) -> int:
        """创建低血糖警报
        
        设计变更说明：
        - 原逻辑会在存在同一风险级别的ACTIVE警报时直接跳过创建，导致后续相同风险被静默忽略
        - 为了确保每次首页/预测触发中高风险时都能产生一条明确的报警记录并发送通知，取消同级别去重逻辑
        """
        try:
            conn = sqlite3.connect(self.get_database_path())
            cursor = conn.cursor()
            
            # 直接创建新警报，不再因为同级别已存在ACTIVE警报而跳过
            cursor.execute("""
                INSERT INTO hypoglycemia_alerts 
                (alert_time, predicted_glucose_mgdl, predicted_glucose_mmol, 
                 risk_level, alert_status, notification_sent)
                VALUES (?, ?, ?, ?, 'ACTIVE', 0)
            """, (
                datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                risk_assessment['predicted_glucose_mgdl'],
                risk_assessment['predicted_glucose_mmol'],
                risk_assessment['risk_level']
            ))
            
            alert_id = cursor.lastrowid
            conn.commit()
            conn.close()
            
            logger.info(f"创建低血糖警报 ID: {alert_id}, 风险级别: {risk_assessment['risk_level']}")
            return alert_id
            
        except Exception as e:
            logger.error(f"创建低血糖警报失败: {e}")
            return -1

    def validate_prediction_result(self, prediction_data: Dict, glucose_data: List[Dict]) -> Dict:
        """预测结果校验机制 - 趋势一致性和历史准确率对比"""
        try:
            predicted_glucose = prediction_data.get('predicted_glucose_mgdl', 0)
            current_glucose = prediction_data.get('current_glucose_mgdl', 0)
            confidence_score = prediction_data.get('confidence_score', 0)
            
            validation_results = {
                'is_valid': True,
                'validation_score': 100.0,
                'validation_flags': [],
                'warnings': [],
                'trend_consistency': 0.0,
                'historical_accuracy': 0.0,
                'physiological_plausibility': 0.0
            }
            
            # 1. 趋势一致性检查
            trend_consistency = self._check_trend_consistency(prediction_data, glucose_data)
            validation_results['trend_consistency'] = trend_consistency['score']
            
            if trend_consistency['score'] < 60:
                validation_results['is_valid'] = False
                validation_results['validation_flags'].append('trend_inconsistency')
                validation_results['warnings'].append(trend_consistency['warning'])
            
            # 2. 历史准确率对比
            historical_accuracy = self._check_historical_accuracy(predicted_glucose, glucose_data)
            validation_results['historical_accuracy'] = historical_accuracy['score']
            
            if historical_accuracy['score'] < 50:
                validation_results['validation_flags'].append('historical_inaccuracy')
                validation_results['warnings'].append(historical_accuracy['warning'])
            
            # 3. 生理合理性检查
            physiological_check = self._check_physiological_plausibility(
                predicted_glucose, current_glucose, glucose_data
            )
            validation_results['physiological_plausibility'] = physiological_check['score']
            
            if physiological_check['score'] < 70:
                validation_results['is_valid'] = False
                validation_results['validation_flags'].append('physiological_implausibility')
                validation_results['warnings'].append(physiological_check['warning'])
            
            # 4. 计算综合验证分数
            weights = {
                'trend_consistency': 0.4,
                'historical_accuracy': 0.3,
                'physiological_plausibility': 0.3
            }
            
            validation_results['validation_score'] = (
                validation_results['trend_consistency'] * weights['trend_consistency'] +
                validation_results['historical_accuracy'] * weights['historical_accuracy'] +
                validation_results['physiological_plausibility'] * weights['physiological_plausibility']
            )
            
            # 5. 基于置信度调整
            if confidence_score < 50:
                validation_results['validation_score'] *= 0.8
                validation_results['warnings'].append('预测置信度过低')
            
            return validation_results
            
        except Exception as e:
            logger.error(f"预测结果校验失败: {e}")
            return {
                'is_valid': False,
                'validation_score': 0.0,
                'validation_flags': ['validation_error'],
                'warnings': [f'校验过程出错: {str(e)}'],
                'trend_consistency': 0.0,
                'historical_accuracy': 0.0,
                'physiological_plausibility': 0.0
            }
    
    def _check_trend_consistency(self, prediction_data: Dict, glucose_data: List[Dict]) -> Dict:
        """检查趋势一致性"""
        try:
            if not glucose_data or len(glucose_data) < 5:
                return {
                    'score': 0.0,
                    'warning': '数据点不足，无法进行趋势一致性检查'
                }
            
            # 获取最近的血糖值和趋势
            sorted_data = sorted(glucose_data, key=lambda x: x.get('shanghai_time', ''))
            recent_values = []
            for entry in sorted_data[-10:]:
                sgv = entry.get('sgv', 0)
                if sgv > 0:
                    recent_values.append(sgv)
            
            if len(recent_values) < 5:
                return {
                    'score': 50.0,
                    'warning': '有效数据点不足，趋势分析受限'
                }
            
            # 计算当前趋势
            recent_trend = self._calculate_trend(recent_values[-5:])
            predicted_glucose = prediction_data.get('predicted_glucose_mgdl', 0)
            current_glucose = prediction_data.get('current_glucose_mgdl', 0)
            
            # 预测的趋势
            predicted_trend = predicted_glucose - current_glucose
            
            # 比较趋势一致性
            if abs(recent_trend - predicted_trend) <= 2:
                trend_score = 100.0
            elif abs(recent_trend - predicted_trend) <= 5:
                trend_score = 80.0
            elif abs(recent_trend - predicted_trend) <= 10:
                trend_score = 60.0
            else:
                trend_score = 30.0
            
            warning_msg = None
            if trend_score < 60:
                direction = "上升" if predicted_trend > 0 else "下降"
                warning_msg = f"预测趋势与近期趋势不一致，预测血糖{direction}过快"
            
            return {
                'score': trend_score,
                'warning': warning_msg or '趋势一致性良好'
            }
            
        except Exception as e:
            logger.error(f"趋势一致性检查失败: {e}")
            return {
                'score': 0.0,
                'warning': f'趋势检查失败: {str(e)}'
            }
    
    def _check_historical_accuracy(self, predicted_glucose: float, glucose_data: List[Dict]) -> Dict:
        """检查历史准确率"""
        try:
            # 获取最近的预测结果进行对比
            conn = sqlite3.connect(self.get_database_path())
            cursor = conn.cursor()
            
            # 获取最近24小时的历史预测
            cursor.execute("""
                SELECT predicted_glucose_mgdl, prediction_time 
                FROM prediction_results 
                WHERE prediction_time >= datetime('now', '-1 day')
                ORDER BY prediction_time DESC LIMIT 10
            """)
            
            historical_predictions = cursor.fetchall()
            conn.close()
            
            if not historical_predictions:
                return {
                    'score': 70.0,
                    'warning': '暂无历史预测数据用于对比'
                }
            
            # 计算历史预测的准确率
            accuracy_scores = []
            for pred_mgdl, pred_time in historical_predictions:
                # 找到预测时间附近的实际血糖值
                actual_glucose = self._find_actual_glucose_at_time(pred_time, glucose_data)
                if actual_glucose > 0:
                    error_percentage = abs(pred_mgdl - actual_glucose) / actual_glucose * 100
                    accuracy = max(0, 100 - error_percentage)
                    accuracy_scores.append(accuracy)
            
            if not accuracy_scores:
                return {
                    'score': 60.0,
                    'warning': '无法找到对应的实际血糖值进行对比'
                }
            
            avg_accuracy = sum(accuracy_scores) / len(accuracy_scores)
            
            warning_msg = None
            if avg_accuracy < 60:
                warning_msg = f'历史预测准确率较低 ({avg_accuracy:.1f}%)，本次预测可能不可靠'
            elif avg_accuracy < 80:
                warning_msg = f'历史预测准确率一般 ({avg_accuracy:.1f}%)'
            
            return {
                'score': avg_accuracy,
                'warning': warning_msg or '历史预测准确率良好'
            }
            
        except Exception as e:
            logger.error(f"历史准确率检查失败: {e}")
            return {
                'score': 50.0,
                'warning': f'历史准确率检查失败: {str(e)}'
            }
    
    def _check_physiological_plausibility(self, predicted_glucose: float, current_glucose: float, glucose_data: List[Dict]) -> Dict:
        """检查生理合理性"""
        try:
            glucose_change = predicted_glucose - current_glucose
            abs_change = abs(glucose_change)
            
            # 生理合理性评分
            plausibility_score = 100.0
            warning_msg = None
            
            # 1. 检查血糖值范围
            if predicted_glucose < 20 or predicted_glucose > 600:
                plausibility_score = 0.0
                warning_msg = '预测血糖值超出生理可能范围'
                return {
                    'score': plausibility_score,
                    'warning': warning_msg
                }
            
            # 2. 检查30分钟内变化幅度
            # 血糖在30分钟内一般不会变化超过50 mg/dL
            if abs_change > 50:
                plausibility_score = 20.0
                warning_msg = '预测血糖变化幅度过大，超出生理正常范围'
            elif abs_change > 30:
                plausibility_score = 50.0
                warning_msg = '预测血糖变化幅度较大，需要谨慎对待'
            elif abs_change > 20:
                plausibility_score = 80.0
                warning_msg = '预测血糖变化幅度中等'
            
            # 3. 检查极端值
            if predicted_glucose < 40:
                plausibility_score = min(plausibility_score, 30.0)
                warning_msg = warning_msg or '预测血糖值极低，存在严重风险'
            elif predicted_glucose < 70:
                plausibility_score = min(plausibility_score, 70.0)
                warning_msg = warning_msg or '预测血糖值偏低，需要关注'
            elif predicted_glucose > 250:
                plausibility_score = min(plausibility_score, 80.0)
                warning_msg = warning_msg or '预测血糖值偏高，需要关注'
            
            # 4. 考虑当前血糖水平对变化幅度的影响
            # 高血糖时变化幅度可能更大
            if current_glucose > 200:
                plausibility_score = min(100.0, plausibility_score * 1.2)
            elif current_glucose < 80:
                # 低血糖时大幅变化更危险
                if abs_change > 15:
                    plausibility_score = min(plausibility_score, 60.0)
            
            return {
                'score': min(100.0, max(0.0, plausibility_score)),
                'warning': warning_msg or '生理合理性检查通过'
            }
            
        except Exception as e:
            logger.error(f"生理合理性检查失败: {e}")
            return {
                'score': 0.0,
                'warning': f'生理合理性检查失败: {str(e)}'
            }
    
    def _calculate_trend(self, values: List[float]) -> float:
        """计算趋势值"""
        if len(values) < 2:
            return 0.0
        
        # 使用线性回归计算趋势
        x = list(range(len(values)))
        y = values
        
        n = len(x)
        sum_x = sum(x)
        sum_y = sum(y)
        sum_xy = sum(xi * yi for xi, yi in zip(x, y))
        sum_x2 = sum(xi * xi for xi in x)
        
        if n * sum_x2 - sum_x * sum_x == 0:
            return 0.0
        
        slope = (n * sum_xy - sum_x * sum_y) / (n * sum_x2 - sum_x * sum_x)
        return slope
    
    def _find_actual_glucose_at_time(self, prediction_time: str, glucose_data: List[Dict]) -> float:
        """找到指定时间附近的实际血糖值"""
        try:
            pred_dt = datetime.strptime(prediction_time, '%Y-%m-%d %H:%M:%S')
            
            for entry in glucose_data:
                time_str = entry.get('shanghai_time', '')
                if time_str:
                    try:
                        entry_dt = datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S')
                        time_diff = abs((entry_dt - pred_dt).total_seconds() / 60)  # 分钟
                        
                        # 寻找10分钟内的实际血糖值
                        if time_diff <= 10:
                            return entry.get('sgv', 0)
                    except ValueError:
                        continue
            
            return 0.0
            
        except Exception as e:
            logger.error(f"查找实际血糖值失败: {e}")
            return 0.0

    def _get_min_data_points_based_on_quality(self, quality_level: str) -> int:
        """根据数据质量等级确定最少数据点要求"""
        quality_requirements = {
            'EXCELLENT': 5,
            'GOOD': 7,
            'FAIR': 10,
            'POOR': 15,
            'CRITICAL': 20
        }
        return quality_requirements.get(quality_level, 10)
    
    def _preprocess_glucose_data(self, sorted_data: List[Dict], quality_assessment: Dict) -> List[Dict]:
        """数据预处理和异常值过滤"""
        try:
            cleaned_data = []
            
            for entry in sorted_data:
                sgv = entry.get('sgv', 0)
                
                # 1. 基本有效性检查
                if sgv <= 0 or sgv > 1000:  # 排除明显无效的值
                    continue
                
                # 2. 基于质量等级的过滤策略
                quality_level = quality_assessment['quality_level']
                
                if quality_level == 'CRITICAL':
                    # 严格模式：只保留最可靠的数据
                    if sgv < 30 or sgv > 500:
                        continue
                elif quality_level == 'POOR':
                    # 较严格模式
                    if sgv < 20 or sgv > 600:
                        continue
                
                # 3. 时间间隔一致性检查
                time_str = entry.get('shanghai_time', '')
                if not time_str:
                    continue
                
                try:
                    datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S')
                except ValueError:
                    continue
                
                cleaned_data.append(entry)
            
            return cleaned_data
            
        except Exception as e:
            logger.error(f"数据预处理失败: {e}")
            return sorted_data  # 预处理失败时返回原数据
    
    def _calculate_individual_data_quality(self, entry: Dict, all_data: List[Dict]) -> float:
        """计算单个数据点的质量分数"""
        try:
            sgv = entry.get('sgv', 0)
            time_str = entry.get('shanghai_time', '')
            
            if sgv <= 0 or not time_str:
                return 0.0
            
            quality_score = 100.0
            now_local = self._now_in_config_timezone()
            
            # 1. 基于数值范围的质量评分
            if sgv < 30 or sgv > 400:
                quality_score *= 0.3
            elif sgv < 50 or sgv > 300:
                quality_score *= 0.7
            elif sgv < 70 or sgv > 250:
                quality_score *= 0.9
            
            # 2. 基于时间新鲜度的评分
            try:
                entry_time = datetime.strptime(time_str, '%Y-%m-%d %H:%M:%S')
                time_diff_minutes = (now_local - entry_time).total_seconds() / 60
                 
                if time_diff_minutes > 120:  # 超过2小时
                    quality_score *= 0.5
                elif time_diff_minutes > 60:  # 超过1小时
                    quality_score *= 0.8
                elif time_diff_minutes > 30:  # 超过30分钟
                    quality_score *= 0.9
            except ValueError:
                quality_score *= 0.7
            
            return max(0.0, min(100.0, quality_score))
            
        except Exception as e:
            logger.error(f"计算单个数据质量失败: {e}")
            return 50.0  # 默认中等质量
    
    def _process_lifestyle_data(self, treatment_data: List[Dict], glucose_data: List[Dict]) -> Dict:
        """处理饮食和运动数据，提取与血糖预测相关的生活方式因素"""
        lifestyle_factors = {
            'recent_meals': [],
            'recent_exercise': [],
            'carb_impact': 0.0,
            'exercise_impact': 0.0,
            'has_lifestyle_data': False
        }
        
        if not treatment_data:
            return lifestyle_factors
        
        try:
            current_time = self._now_in_config_timezone()
            # 考虑4小时内的饮食影响和2小时内的运动影响
            meal_cutoff = current_time - timedelta(hours=4)
            exercise_cutoff = current_time - timedelta(hours=2)
            
            for entry in treatment_data:
                entry_time_str = entry.get('shanghai_time', '')
                if not entry_time_str:
                    continue
                    
                try:
                    entry_time = datetime.strptime(entry_time_str, '%Y-%m-%d %H:%M:%S')
                    event_type = entry.get('eventType', '').lower()
                    
                    # 处理饮食数据
                    if any(food_keyword in event_type for food_keyword in ['餐', '食', 'breakfast', 'lunch', 'dinner', 'snack']):
                        if entry_time >= meal_cutoff:
                            carbs = entry.get('carbs', 0) or entry.get('carbohydrates', 0)
                            protein = entry.get('protein', 0) or 0
                            fat = entry.get('fat', 0) or 0
                            
                            meal_data = {
                                'time': entry_time_str,
                                'minutes_ago': int((current_time - entry_time).total_seconds() / 60),
                                'carbs': carbs,
                                'protein': protein,
                                'fat': fat,
                                'total_calories': carbs * 4 + protein * 4 + fat * 9  # 简单卡路里计算
                            }
                            lifestyle_factors['recent_meals'].append(meal_data)
                            
                            # 碳水影响估算：每克碳水预期在2-3小时内提升血糖1-2 mg/dL
                            if carbs > 0:
                                time_decay = max(0.1, 1.0 - meal_data['minutes_ago'] / 240.0)  # 4小时衰减
                                lifestyle_factors['carb_impact'] += carbs * 1.2 * time_decay * (0.8 + 0.2 * time_decay)
                    
                    # 处理运动数据
                    elif any(exercise_keyword in event_type for exercise_keyword in ['运动', 'exercise', 'activity', 'run', 'walk', 'gym']):
                        if entry_time >= exercise_cutoff:
                            duration = entry.get('duration', 0) or 0
                            notes = entry.get('notes', '') or ''
                            
                            exercise_data = {
                                'time': entry_time_str,
                                'minutes_ago': int((current_time - entry_time).total_seconds() / 60),
                                'duration': duration,
                                'intensity': self._estimate_exercise_intensity(event_type, notes, duration)
                            }
                            lifestyle_factors['recent_exercise'].append(exercise_data)
                            
                            # 运动影响估算：中等强度运动每小时降低血糖10-20 mg/dL
                            if duration > 0:
                                time_decay = max(0.1, 1.0 - exercise_data['minutes_ago'] / 120.0)  # 2小时衰减
                                intensity_factor = exercise_data['intensity']
                                lifestyle_factors['exercise_impact'] -= duration * 0.2 * intensity_factor * time_decay * (0.7 + 0.3 * time_decay)
                            
                except (ValueError, TypeError) as e:
                    logger.warning(f"处理生活方式数据时时间解析失败: {entry_time_str}, 错误: {e}")
                    continue
            
            lifestyle_factors['has_lifestyle_data'] = bool(
                lifestyle_factors['recent_meals'] or lifestyle_factors['recent_exercise']
            )
            
            logger.info(f"生活方式因素分析: 饮食影响={lifestyle_factors['carb_impact']:.2f}, "
                       f"运动影响={lifestyle_factors['exercise_impact']:.2f}, "
                       f"最近餐食={len(lifestyle_factors['recent_meals'])}, "
                       f"最近运动={len(lifestyle_factors['recent_exercise'])}")
            
            return lifestyle_factors
            
        except Exception as e:
            logger.error(f"处理生活方式数据失败: {e}")
            return lifestyle_factors
    
    def _estimate_exercise_intensity(self, event_type: str, notes: str, duration: int) -> float:
        """根据运动类型和时长估算运动强度"""
        intensity = 1.0  # 默认中等强度
        
        event_type_lower = event_type.lower()
        notes_lower = notes.lower()
        
        # 根据运动类型调整强度
        if any(high_keyword in event_type_lower for high_keyword in ['跑', 'run', '高强度', 'hiit']):
            intensity = 1.5
        elif any(medium_keyword in event_type_lower for medium_keyword in ['走', 'walk', '快走', '游泳', 'swim']):
            intensity = 1.2
        elif any(low_keyword in event_type_lower for low_keyword in ['瑜伽', 'yoga', 'stretch', '散步']):
            intensity = 0.8
        
        # 根据备注调整
        if any(high_note in notes_lower for high_note in ['剧烈', '累', ' tired', '高强度']):
            intensity = max(intensity, 1.4)
        elif any(low_note in notes_lower for low_note in ['轻松', '放松', 'light', 'easy']):
            intensity = min(intensity, 0.9)
        
        # 根据时长调整（过短或过长的运动强度适当降低）
        if duration < 10:
            intensity *= 0.8
        elif duration > 120:
            intensity *= 0.9
        
        return intensity
    
    def _calculate_lifestyle_adjustment(self, lifestyle_factors: Dict, minutes_ahead: int) -> float:
        """基于生活方式数据计算预测调整值"""
        if not lifestyle_factors['has_lifestyle_data']:
            return 0.0
        
        # 时间衰减因子：预测时间越远，生活方式影响越小
        time_decay = max(0.1, 1.0 - minutes_ahead / 60.0)
        
        # 总生活方式调整 = 饮食影响 + 运动影响
        total_adjustment = (lifestyle_factors['carb_impact'] + lifestyle_factors['exercise_impact']) * time_decay
        
        return total_adjustment

    def _calculate_enhanced_trend(self, glucose_values: List[float], quality_scores: List[float], quality_assessment: Dict, lifestyle_factors: Dict = None) -> Dict:
        """基于数据质量和新鲜度的动态权重趋势计算"""
        try:
            if len(glucose_values) < 2:
                return {
                    'avg_change': 0.0,
                    'weights': [],
                    'trend_confidence': 0.5
                }
            
            # 1. 计算动态权重
            weights = self._calculate_dynamic_weights(glucose_values, quality_scores)
            
            # 2. 使用加权平均计算趋势
            changes = []
            weighted_changes = []
            
            for i in range(1, len(glucose_values)):
                change = glucose_values[i] - glucose_values[i-1]
                changes.append(change)
                
                # 使用权重调整变化率
                weight_factor = (weights[i] + weights[i-1]) / 2
                weighted_changes.append(change * weight_factor)
            
            if not weighted_changes:
                return {
                    'avg_change': 0.0,
                    'weights': weights,
                    'trend_confidence': 0.5
                }
            
            # 3. 计算加权平均变化率
            avg_change = sum(weighted_changes) / len(weighted_changes)
            
            # 4. 计算趋势置信度
            trend_confidence = self._calculate_trend_confidence(changes, weights, quality_assessment)
            
            return {
                'avg_change': avg_change,
                'weights': weights,
                'trend_confidence': trend_confidence,
                'raw_changes': changes,
                'weighted_changes': weighted_changes
            }
            
        except Exception as e:
            logger.error(f"增强趋势计算失败: {e}")
            return {
                'avg_change': 0.0,
                'weights': [1.0] * len(glucose_values),
                'trend_confidence': 0.5
            }
    
    def _calculate_dynamic_weights(self, glucose_values: List[float], quality_scores: List[float]) -> List[float]:
        """计算基于数据质量和新鲜度的动态权重"""
        try:
            weights = []
            n = len(glucose_values)
            
            # 1. 基础权重：指数衰减（越新的数据权重越高）
            base_weights = []
            for i in range(n):
                # 指数衰减因子：最新数据权重为1，最旧数据权重为0.3
                decay_factor = 0.3 + 0.7 * (i / (n - 1)) if n > 1 else 1.0
                base_weights.append(decay_factor)
            
            # 2. 质量调整权重
            for i in range(n):
                quality_score = quality_scores[i] if i < len(quality_scores) else 50.0
                quality_factor = quality_score / 100.0
                
                # 综合权重 = 基础权重 × 质量因子
                combined_weight = base_weights[i] * quality_factor
                weights.append(combined_weight)
            
            # 3. 归一化权重
            total_weight = sum(weights)
            if total_weight > 0:
                weights = [w / total_weight * len(weights) for w in weights]  # 保持平均权重为1
            else:
                weights = [1.0] * len(weights)
            
            return weights
            
        except Exception as e:
            logger.error(f"动态权重计算失败: {e}")
            return [1.0] * len(glucose_values)
    
    def _calculate_trend_confidence(self, changes: List[float], weights: List[float], quality_assessment: Dict) -> float:
        """计算趋势置信度"""
        try:
            if not changes:
                return 0.5
            
            # 1. 基于变化一致性的置信度
            if len(changes) > 1:
                variance = sum((x - sum(changes) / len(changes)) ** 2 for x in changes) / len(changes)
                std_dev = variance ** 0.5
                avg_change = sum(changes) / len(changes)
                
                if avg_change != 0:
                    consistency_confidence = max(0, 1 - (std_dev / abs(avg_change)))
                else:
                    consistency_confidence = 1.0
            else:
                consistency_confidence = 0.5
            
            # 2. 基于数据质量的置信度调整
            quality_level = quality_assessment['quality_level']
            quality_multiplier = {
                'EXCELLENT': 1.0,
                'GOOD': 0.9,
                'FAIR': 0.8,
                'POOR': 0.6,
                'CRITICAL': 0.4
            }
            
            quality_adjustment = quality_multiplier.get(quality_level, 0.8)
            
            # 3. 计算最终趋势置信度
            trend_confidence = consistency_confidence * quality_adjustment
            
            return max(0.1, min(1.0, trend_confidence))
            
        except Exception as e:
            logger.error(f"趋势置信度计算失败: {e}")
            return 0.5
    
    def _calculate_enhanced_confidence(self, glucose_values: List[float], data_quality_scores: List[float], 
                                      quality_assessment: Dict, trend_confidence: float, 
                                      current_glucose: float, all_glucose_data: List[Dict]) -> Dict:
        """改进的置信度计算模型 - 考虑多维度因素"""
        try:
            # 1. 数据量因子
            data_points_factor = min(len(glucose_values) / 15, 1.0)  # 基准为15个数据点
            
            # 2. 数据质量因子
            overall_quality_score = quality_assessment['overall_score'] / 100.0
            data_quality_factor = overall_quality_score
            
            # 3. 趋势一致性因子
            trend_consistency_factor = trend_confidence
            
            # 4. 时间段模式因子
            time_pattern_factor = self._calculate_time_pattern_factor(all_glucose_data)
            
            # 5. 用餐/胰岛素影响因子
            meal_insulin_factor = self._calculate_meal_insulin_factor(all_glucose_data, current_glucose)
            
            # 6. 历史准确率因子
            historical_accuracy_factor = self._calculate_historical_accuracy_factor()
            
            # 7. 权重分配
            weights = {
                'data_points': 0.15,
                'data_quality': 0.25,
                'trend_consistency': 0.20,
                'time_pattern': 0.15,
                'meal_insulin': 0.15,
                'historical_accuracy': 0.10
            }
            
            # 8. 计算综合置信度
            final_confidence = (
                data_points_factor * weights['data_points'] +
                data_quality_factor * weights['data_quality'] +
                trend_consistency_factor * weights['trend_consistency'] +
                time_pattern_factor * weights['time_pattern'] +
                meal_insulin_factor * weights['meal_insulin'] +
                historical_accuracy_factor * weights['historical_accuracy']
            )
            
            # 9. 确保置信度在合理范围内
            final_confidence = max(10.0, min(100.0, final_confidence * 100))
            
            return {
                'final_score': round(final_confidence, 1),
                'breakdown': {
                    'data_points_factor': round(data_points_factor, 3),
                    'data_quality_factor': round(data_quality_factor, 3),
                    'trend_consistency_factor': round(trend_consistency_factor, 3),
                    'time_pattern_factor': round(time_pattern_factor, 3),
                    'meal_insulin_factor': round(meal_insulin_factor, 3),
                    'historical_accuracy_factor': round(historical_accuracy_factor, 3)
                },
                'weights': weights
            }
            
        except Exception as e:
            logger.error(f"增强置信度计算失败: {e}")
            return {
                'final_score': 50.0,
                'breakdown': {},
                'weights': {}
            }
    
    def _calculate_time_pattern_factor(self, glucose_data: List[Dict]) -> float:
        """计算时间段模式因子"""
        try:
            if not glucose_data:
                return 0.8
            
            current_hour = datetime.now().hour
            
            # 定义不同时间段的预测可靠性
            time_patterns = {
                'night': list(range(0, 6)),      # 凌晨0-5点，稳定性高
                'morning': list(range(6, 12)),    # 早晨6-11点，变化较快
                'afternoon': list(range(12, 18)), # 下午12-17点，相对稳定
                'evening': list(range(18, 24))    # 晚上18-23点，变化较复杂
            }
            
            # 为不同时间段分配可靠性分数
            pattern_reliability = {
                'night': 0.95,      # 夜间血糖相对稳定
                'morning': 0.75,     # 早晨变化快，可靠性较低
                'afternoon': 0.85,   # 下午相对稳定
                'evening': 0.80      # 晚间变化较复杂
            }
            
            for period, hours in time_patterns.items():
                if current_hour in hours:
                    return pattern_reliability[period]
            
            return 0.8  # 默认值
            
        except Exception as e:
            logger.error(f"时间段模式计算失败: {e}")
            return 0.8
    
    def _calculate_meal_insulin_factor(self, glucose_data: List[Dict], current_glucose: float) -> float:
        """计算用餐/胰岛素影响因子"""
        try:
            # 简化版本：基于当前血糖水平和时间推断可能的用餐/胰岛素影响
            current_hour = datetime.now().hour
            
            factor = 1.0
            
            # 1. 餐后时间段（通常血糖变化较大）
            if 7 <= current_hour <= 10:  # 早餐后
                factor *= 0.85
            elif 12 <= current_hour <= 14:  # 午餐后
                factor *= 0.85
            elif 18 <= current_hour <= 20:  # 晚餐后
                factor *= 0.85
            
            # 2. 基于当前血糖水平调整
            if current_glucose > 180:  # 高血糖时，预测可靠性降低
                factor *= 0.9
            elif current_glucose < 70:  # 低血糖时，预测可靠性降低
                factor *= 0.8
            
            return max(0.5, min(1.0, factor))
            
        except Exception as e:
            logger.error(f"用餐胰岛素因子计算失败: {e}")
            return 0.9
    
    def _calculate_historical_accuracy_factor(self) -> float:
        """计算历史准确率因子"""
        try:
            # 获取最近的预测准确率
            conn = sqlite3.connect(self.get_database_path())
            cursor = conn.cursor()
            
            # 获取最近24小时的预测结果
            cursor.execute("""
                SELECT predicted_glucose_mgdl, prediction_time 
                FROM prediction_results 
                WHERE prediction_time >= datetime('now', '-1 day')
                ORDER BY prediction_time DESC LIMIT 20
            """)
            
            predictions = cursor.fetchall()
            conn.close()
            
            if not predictions:
                return 0.8  # 没有历史数据时的默认值
            
            # 简化版本：基于预测数量评估
            # 实际应用中应该与实际血糖值对比计算准确率
            prediction_count = len(predictions)
            
            if prediction_count >= 15:
                return 0.9
            elif prediction_count >= 10:
                return 0.85
            elif prediction_count >= 5:
                return 0.8
            else:
                return 0.75
            
        except Exception as e:
            logger.error(f"历史准确率因子计算失败: {e}")
            return 0.8

    def get_user_alert_config(self) -> Dict:
        """获取用户警报配置"""
        try:
            conn = sqlite3.connect(self.get_database_path())
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT high_risk_threshold_mgdl, medium_risk_threshold_mgdl, 
                       enable_predictions, enable_alerts, notification_methods, enable_email_alerts, enable_xxtui_alerts
                FROM user_alert_config 
                WHERE id = 1
            """)
            
            result = cursor.fetchone()
            conn.close()
            
            if result:
                return {
                    'high_risk_threshold_mgdl': result[0],
                    'medium_risk_threshold_mgdl': result[1],
                    'enable_predictions': bool(result[2]),
                    'enable_alerts': bool(result[3]),
                    'notification_methods': result[4],
                    'enable_email_alerts': bool(result[5]) if result[5] is not None else False,
                    'enable_xxtui_alerts': bool(result[6]) if result[6] is not None else False
                }
            else:
                # 返回默认配置
                return {
                    'high_risk_threshold_mgdl': 70,
                    'medium_risk_threshold_mgdl': 80,
                    'enable_predictions': True,
                    'enable_alerts': True,
                    'notification_methods': 'web',
                    'enable_email_alerts': False,
                    'enable_xxtui_alerts': False
                }
                
        except Exception as e:
            logger.error(f"获取用户警报配置失败: {e}")
            return {
                'high_risk_threshold_mgdl': 70,
                'medium_risk_threshold_mgdl': 80,
                'enable_predictions': True,
                'enable_alerts': True,
                'notification_methods': 'web',
                'enable_email_alerts': False,
                'enable_xxtui_alerts': False
            }

    def update_user_alert_config(self, config: Dict) -> bool:
        """更新用户警报配置"""
        try:
            conn = sqlite3.connect(self.get_database_path())
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE user_alert_config 
                SET high_risk_threshold_mgdl = ?, 
                    medium_risk_threshold_mgdl = ?,
                    enable_predictions = ?,
                    enable_alerts = ?,
                    notification_methods = ?,
                    enable_email_alerts = ?,
                    enable_xxtui_alerts = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = 1
            """, (
                config.get('high_risk_threshold_mgdl', 70),
                config.get('medium_risk_threshold_mgdl', 80),
                1 if config.get('enable_predictions', True) else 0,
                1 if config.get('enable_alerts', True) else 0,
                config.get('notification_methods', 'web'),
                1 if config.get('enable_email_alerts', False) else 0,
                1 if config.get('enable_xxtui_alerts', False) else 0
            ))
            
            conn.commit()
            conn.close()
            logger.info("用户警报配置已更新")
            return True
            
        except Exception as e:
            logger.error(f"更新用户警报配置失败: {e}")
            return False

    def get_alert_history(self, limit: int = 50) -> List[Dict]:
        """获取警报历史"""
        try:
            conn = sqlite3.connect(self.get_database_path())
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT id, alert_time, predicted_glucose_mgdl, predicted_glucose_mmol, 
                       risk_level, alert_status, acknowledged_at, notification_sent
                FROM hypoglycemia_alerts 
                ORDER BY created_at DESC 
                LIMIT ?
            """, (limit,))
            
            rows = cursor.fetchall()
            conn.close()
            
            alerts = []
            for row in rows:
                alerts.append({
                    'id': row[0],
                    'alert_time': row[1],
                    'predicted_glucose_mgdl': row[2],
                    'predicted_glucose_mmol': row[3],
                    'risk_level': row[4],
                    'alert_status': row[5],
                    'acknowledged_at': row[6],
                    'notification_sent': bool(row[7])
                })
            
            return alerts
            
        except Exception as e:
            logger.error(f"获取警报历史失败: {e}")
            return []

    def acknowledge_alert(self, alert_id: int) -> bool:
        """确认警报"""
        try:
            conn = sqlite3.connect(self.get_database_path())
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE hypoglycemia_alerts 
                SET alert_status = 'ACKNOWLEDGED', 
                    acknowledged_at = CURRENT_TIMESTAMP
                WHERE id = ? AND alert_status = 'ACTIVE'
            """, (alert_id,))
            
            affected_rows = cursor.rowcount
            conn.commit()
            conn.close()
            
            if affected_rows > 0:
                logger.info(f"警报 {alert_id} 已确认")
                return True
            else:
                logger.warning(f"未找到活跃的警报 {alert_id}")
                return False
                
        except Exception as e:
            logger.error(f"确认警报失败: {e}")
            return False

# 创建全局实例
monitor = NightscoutWebMonitor()

@app.before_request
def require_login():
    """在每个请求前检查是否需要登录"""
    if monitor.config.get('auth', {}).get('enable'):
        allowed_routes = ['login', 'static']
        if 'logged_in' not in session and request.endpoint not in allowed_routes:
            return redirect(url_for('login', next=request.url))
# Flask 路由
@app.route('/')
def index():
    """主页 - 显示血糖数据表格"""
    return render_template('index.html')

@app.route('/messages')
def messages_page():
    """消息收件箱页面"""
    return render_template('messages.html', unread_count=monitor.get_unread_message_count())

@app.route('/config')
def config_page():
    """配置页面"""
    # 确保 config 对象包含 treatment_plan 字段
    config = monitor.config.copy()
    if 'treatment_plan' not in config:
        config['treatment_plan'] = {
            'medications': [],
            'insulin_enabled': False,
            'insulin_dosage': 0,
            'insulin_frequency': '',
            'insulin_custom_frequency': ''
        }
    return render_template('config.html', config=config)

@app.route('/api/glucose-data')
def api_glucose_data():
    """获取血糖数据API"""
    days = request.args.get('days', 7, type=int)
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    glucose_data = monitor.get_glucose_data_from_db(days=days, start_date=start_date, end_date=end_date)

    # 转换数据格式用于前端显示
    formatted_data = []
    for entry in glucose_data:
        formatted_data.append({
            'time': entry.get('shanghai_time', ''),
            'value_mgdl': entry.get('sgv', 0),
            'value_mmol': monitor.mg_dl_to_mmol_l(entry.get('sgv', 0)),
            'direction': entry.get('direction', ''),
            'trend': entry.get('trend', 0)
        })

    return jsonify(formatted_data)

@app.route('/api/treatment-data')
def api_treatment_data():
    """获取治疗数据API"""
    days = request.args.get('days', 7, type=int)
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    treatment_data = monitor.get_treatment_data_from_db(days=days, start_date=start_date, end_date=end_date)

    # 转换数据格式用于前端显示
    formatted_data = []
    for entry in treatment_data:
        formatted_data.append({
            'time': entry.get('shanghai_time', ''),
            'event_type': entry.get('eventType', ''),
            'carbs': entry.get('carbs', 0),
            'protein': entry.get('protein', 0),
            'fat': entry.get('fat', 0),
            'insulin': entry.get('insulin', 0),
            'notes': entry.get('notes', ''),
            'duration': entry.get('duration', 0)
        })

    return jsonify(formatted_data)

@app.route('/api/activity-data')
def api_activity_data():
    """获取运动数据API"""
    days = request.args.get('days', 7, type=int)
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    activity_data = monitor.get_activity_data_from_db(days=days, start_date=start_date, end_date=end_date)

    # 转换数据格式用于前端显示
    formatted_data = []
    for entry in activity_data:
        formatted_data.append({
            'time': entry.get('shanghai_time', ''),
            'event_type': entry.get('eventType', ''),
            'duration': entry.get('duration', 0),
            'notes': entry.get('notes', '')
        })

    return jsonify(formatted_data)

@app.route('/api/meter-data')
def api_meter_data():
    """获取指尖血糖数据API - 和报表逻辑完全一致"""
    days = request.args.get('days', 7, type=int)
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    # 直接获取数据，和报表逻辑完全一致
    meter_data = monitor.get_meter_data_from_db(days=days, start_date=start_date, end_date=end_date)
    
    # 转换数据格式用于前端显示 - 完全对标报表逻辑
    formatted_data = []
    for entry in meter_data:
        if entry.get("sgv"):
            # 确保指尖血糖数据以mmol/L单位处理 - 和报表完全一致
            mmol_value = float(entry["sgv"])
            
            # 寻找最接近的CGM血糖值
            cgm_mmol = None
            try:
                conn = sqlite3.connect(monitor.get_database_path())
                cursor = conn.cursor()
                
                meter_time_str = entry.get('date_string', '')
                if meter_time_str:
                    query = """
                        SELECT sgv
                        FROM glucose_data
                        ORDER BY ABS(julianday(?) - julianday(date_string))
                        LIMIT 1
                    """
                    cursor.execute(query, (meter_time_str,))
                    closest_cgm_row = cursor.fetchone()
                    
                    if closest_cgm_row and closest_cgm_row[0]:
                        cgm_mmol = monitor.mg_dl_to_mmol_l(closest_cgm_row[0])
                
                conn.close()
            except Exception as e:
                logger.error(f"获取CGM血糖值失败: {e}")
                if 'conn' in locals():
                    conn.close()
            
            formatted_data.append({
                'time': entry.get('shanghai_time', ''),
                'value_mmol': mmol_value,
                'cgm_value_mmol': cgm_mmol
            })
    
    return jsonify(formatted_data)

@app.route('/api/statistics')
def api_statistics():
    """获取血糖统计数据API"""
    days = request.args.get('days', 7, type=int)
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    glucose_data = monitor.get_glucose_data_from_db(days=days, start_date=start_date, end_date=end_date)

    if not glucose_data:
        return jsonify({'error': '暂无血糖数据'}), 404

    # 提取血糖值
    values = []
    for entry in glucose_data:
        if entry.get("sgv"):
            mmol_value = monitor.mg_dl_to_mmol_l(entry["sgv"])
            values.append(mmol_value)

    if not values:
        return jsonify({'error': '血糖数据格式错误'}), 404

    # 计算基础统计
    avg_glucose = sum(values) / len(values)
    max_glucose = max(values)
    min_glucose = min(values)

    # 计算目标范围内的比例
    in_range_count = sum(1 for v in values if 3.9 <= v <= 10.0)
    in_range_percentage = (in_range_count / len(values)) * 100

    # 计算糖化血红蛋白估算
    hba1c_data = monitor.calculate_estimated_hba1c(values)

    # 计算血糖变异系数
    cv_data = monitor.calculate_glucose_cv(values)

    return jsonify({
        'avg_glucose': round(avg_glucose, 1),
        'max_glucose': round(max_glucose, 1),
        'min_glucose': round(min_glucose, 1),
        'in_range_percentage': round(in_range_percentage, 1),
        'in_range_count': in_range_count,
        'total_count': len(values),
        'hba1c_data': hba1c_data,
        'cv_data': cv_data,
        'days': days
    })

@app.route('/api/current-glucose')
def api_current_glucose():
    """获取当前血糖数据API"""
    try:
        # 获取最近的一条血糖数据
        glucose_data = monitor.get_glucose_data_from_db(days=1)
        if not glucose_data:
            return jsonify({'error': '暂无血糖数据'}), 404

        latest_entry = glucose_data[0]
        
        formatted_entry = {
            'time': latest_entry.get('shanghai_time', ''),
            'value_mgdl': latest_entry.get('sgv', 0),
            'value_mmol': monitor.mg_dl_to_mmol_l(latest_entry.get('sgv', 0)),
            'direction': latest_entry.get('direction', ''),
            'trend': latest_entry.get('trend', 0)
        }
        return jsonify(formatted_entry)
    except Exception as e:
        logger.error(f"获取当前血糖数据失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/analysis')
def api_analysis():
    """获取AI分析API"""
    try:
        # 获取当前时间，格式化为 YYYY-MM-DD HH:MM:SS
        current_time = monitor._now_in_config_timezone()
        today = current_time.strftime('%Y-%m-%d')
        current_time_str = current_time.strftime('%Y-%m-%d %H:%M:%S')
        
        logger.info(f"开始执行手动分析，时间范围：{today} 00:00:00 到 {current_time_str}")
        
        # 使用与首页相同的数据获取方式 - 从本地数据库获取当天数据
        today_start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = current_time
        
        # 获取当天数据（与首页API逻辑一致）
        glucose_data = monitor.get_glucose_data_from_db(start_date=today, end_date=today)
        treatment_data = monitor.get_treatment_data_from_db(start_date=today, end_date=today)
        activity_data = monitor.get_activity_data_from_db(start_date=today, end_date=today)
        meter_data = monitor.get_meter_data_from_db(start_date=today, end_date=today)
        
        # 过滤数据，只保留从00:00到当前时间的数据 - 使用统一时间解析
        def filter_data_by_time(data_list):
            """使用统一时间解析函数过滤数据"""
            filtered_data = []
            for item in data_list:
                if item.get('shanghai_time'):
                    try:
                        time_dt = monitor.parse_time_string(item['shanghai_time'])
                        if today_start <= time_dt <= today_end:
                            filtered_data.append(item)
                    except (ValueError, TypeError):
                        logger.warning(f"数据时间解析失败: {item.get('shanghai_time')}")
                        continue
            return filtered_data
        
        glucose_data = filter_data_by_time(glucose_data)
        treatment_data = filter_data_by_time(treatment_data)
        activity_data = filter_data_by_time(activity_data)
        meter_data = filter_data_by_time(meter_data)
        
        logger.info(f"手动分析过滤后数据条数 - 血糖: {len(glucose_data) if glucose_data else 0}, "
                   f"治疗: {len(treatment_data) if treatment_data else 0}, "
                   f"活动: {len(activity_data) if activity_data else 0}, "
                   f"血糖仪: {len(meter_data) if meter_data else 0}")

        if not glucose_data:
            return jsonify({'error': '暂无血糖数据'}), 404

        try:
            # 使用与自动分析相同的分析逻辑
            analysis = asyncio.run(monitor.get_ai_analysis(glucose_data, treatment_data, activity_data, meter_data, 1, use_time_window=True))
            # 保存分析结果到消息表
            monitor.save_message("analysis", "血糖分析报告", analysis)
            return jsonify({'analysis': analysis})
        except RuntimeError as e:
            # 处理在非主线程中运行asyncio.run可能出现的问题
            if "cannot run loop while another loop is running" in str(e):
                loop = asyncio.get_event_loop()
                analysis = loop.run_until_complete(monitor.get_ai_analysis(glucose_data, treatment_data, activity_data, meter_data, 1, use_time_window=True))
                # 保存分析结果到消息表
                monitor.save_message("analysis", "血糖分析报告", analysis)
                return jsonify({'analysis': analysis})
            else:
                raise e
    except Exception as e:
        logger.error(f"获取分析失败: {e}")
        return jsonify({'error': '分析服务暂时不可用'}), 500

@app.route('/api/ai-consult', methods=['POST'])
def api_ai_consult():
    """AI咨询API"""
    data = request.get_json()
    if not data or 'question' not in data:
        return jsonify({'error': '缺少问题参数'}), 400

    question = data['question']
    question = data['question']
    include_data = data.get('include_data', True)
    try:
        days = int(data.get('days', 7))
    except (ValueError, TypeError):
        days = 7

    try:
        response = asyncio.run(monitor.get_ai_consultation(question, include_data, days))
        # 保存咨询结果到消息表
        monitor.save_message("consultation", f"AI咨询: {question[:30]}...", response)
        return jsonify({'response': response})
    except RuntimeError as e:
        # 处理在非主线程中运行asyncio.run可能出现的问题
        if "cannot run loop while another loop is running" in str(e):
            loop = asyncio.get_event_loop()
            response = loop.run_until_complete(monitor.get_ai_consultation(question, include_data, days))
            # 保存咨询结果到消息表
            monitor.save_message("consultation", f"AI咨询: {question[:30]}...", response)
            return jsonify({'response': response})
        else:
            raise e
    except Exception as e:
        logger.error(f"获取AI咨询失败: {e}")
        return jsonify({'error': 'AI咨询服务暂时不可用'}), 500

@app.route('/api/sync', methods=['POST'])
def api_sync():
    """手动同步数据API"""
    try:
        days = request.json.get('days', 7) if request.json else 7

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=days-1)).strftime('%Y-%m-%d')

        glucose_data, treatment_data, activity_data, meter_data = loop.run_until_complete(
            monitor.fetch_nightscout_data(start_date, end_date)
        )

        if glucose_data:
            loop.run_until_complete(monitor.save_glucose_data(glucose_data))
        if treatment_data:
            loop.run_until_complete(monitor.save_treatment_data(treatment_data))
        if activity_data:
            loop.run_until_complete(monitor.save_activity_data(activity_data))
        if meter_data:
            loop.run_until_complete(monitor.save_meter_data(meter_data))

        loop.close()

        return jsonify({
            'success': True,
            'glucose_count': len(glucose_data),
            'treatment_count': len(treatment_data),
            'activity_count': len(activity_data),
            'meter_count': len(meter_data)
        })

    except Exception as e:
        logger.error(f"同步数据失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/config', methods=['GET', 'POST'])
def api_config():
    """配置管理API"""
    if request.method == 'GET':
        return jsonify(monitor.config)

    elif request.method == 'POST':
        try:
            new_config = request.json
            
            # 如果密码字段为空，则保留旧密码
            if 'auth' in new_config and 'password' in new_config['auth']:
                if not new_config['auth']['password']:
                    new_config['auth']['password'] = monitor.config.get('auth', {}).get('password', '')

            # 处理alert配置 - 更新数据库中的警报配置
            if 'alert' in new_config:
                alert_config = new_config['alert']
                # 转换mmol/L为mg/dL - 正确的阈值映射
                high_risk_threshold_mgdl = alert_config.get('low_glucose_threshold', 3.9) * 18.0
                medium_risk_threshold_mgdl = (alert_config.get('low_glucose_threshold', 3.9) + 0.5) * 18.0
                
                # 更新警报配置
                monitor.update_user_alert_config({
                    'high_risk_threshold_mgdl': high_risk_threshold_mgdl,
                    'medium_risk_threshold_mgdl': medium_risk_threshold_mgdl,
                    'enable_predictions': True,
                    'enable_alerts': True,
                    'notification_methods': 'web',
                    'enable_email_alerts': alert_config.get('enable_email_alerts', False),
                    'enable_xxtui_alerts': alert_config.get('enable_xxtui_alerts', False)
                })

            if monitor.save_config(new_config):
                # 重新加载调度器以应用更改
                schedule_lib.clear()
                monitor.setup_scheduler()
                return jsonify({'success': True})
            else:
                return jsonify({'error': '保存配置失败'}), 500
        except Exception as e:
            logger.error(f"更新配置失败: {e}")
            return jsonify({'error': str(e)}), 500

@app.route('/api/test-connection', methods=['POST'])
def api_test_connection():
    """测试Nightscout连接API"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # 测试获取最近1天的数据
        today = datetime.now().strftime('%Y-%m-%d')
        glucose_data, treatment_data, activity_data, meter_data = loop.run_until_complete(
            monitor.fetch_nightscout_data(today, today)
        )

        loop.close()

        if glucose_data or treatment_data or activity_data or meter_data:
            return jsonify({
                'success': True,
                'message': 'Nightscout连接正常',
                'glucose_count': len(glucose_data),
                'treatment_count': len(treatment_data),
                'activity_count': len(activity_data),
                'meter_count': len(meter_data)
            })
        else:
            return jsonify({
                'success': False,
                'message': 'Nightscout连接失败或无数据'
            })

    except Exception as e:
        logger.error(f"测试连接失败: {e}")
        return jsonify({
            'success': False,
            'message': f'连接失败: {str(e)}'
        })

@app.route('/api/test-email', methods=['POST'])
def api_test_email():
    """测试邮件配置"""
    try:
        result = monitor.test_email_configuration()
        return jsonify(result)
    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"测试失败: {str(e)}"
        })

@app.route('/api/test-xxtui', methods=['POST'])
def api_test_xxtui():
    """测试XXTUI配置"""
    try:
        result = monitor.test_xxtui_configuration()
        return jsonify(result)
    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"测试失败: {str(e)}"
        })

@app.route('/api/validate-email-config', methods=['POST'])
def api_validate_email_config():
    """邮件配置详细验证和诊断"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                "success": False,
                "error": "请提供邮件配置数据"
            })

        email_config = data.get('email', {})
        diagnosis = {
            "success": False,
            "diagnosis": {},
            "recommendations": [],
            "configuration_details": {}
        }

        # 1. 配置完整性检查
        required_fields = ["smtp_server", "smtp_username", "smtp_password", "from_email", "to_emails"]
        missing_fields = [field for field in required_fields if not email_config.get(field)]
        
        if missing_fields:
            diagnosis["diagnosis"]["config_completeness"] = {
                "status": "failed",
                "message": f"缺少必要配置: {', '.join(missing_fields)}"
            }
            diagnosis["recommendations"].append(f"请补充缺失的配置项: {', '.join(missing_fields)}")
            return jsonify(diagnosis)

        diagnosis["diagnosis"]["config_completeness"] = {
            "status": "passed",
            "message": "配置完整性检查通过"
        }

        # 2. 配置详情记录
        diagnosis["configuration_details"] = {
            "smtp_server": email_config["smtp_server"],
            "smtp_port": email_config.get("smtp_port", 587),
            "smtp_username": email_config["smtp_username"],
            "from_email": email_config["from_email"],
            "to_emails": email_config["to_emails"],
            "port_type": "SSL (465)" if email_config.get("smtp_port", 587) == 465 else "STARTTLS (587)"
        }

        # 3. 邮箱格式验证
        from_email = email_config["from_email"]
        if not monitor.validate_email_format(from_email):
            diagnosis["diagnosis"]["email_format"] = {
                "status": "failed",
                "message": "发件人邮箱格式不正确"
            }
            diagnosis["recommendations"].append("请检查发件人邮箱格式")
            return jsonify(diagnosis)

        to_emails = email_config["to_emails"]
        if isinstance(to_emails, list):
            invalid_emails = []
            for email in to_emails:
                if isinstance(email, str):
                    email_clean = email.strip('"\'')
                    if not monitor.validate_email_format(email_clean):
                        invalid_emails.append(email_clean)
                else:
                    invalid_emails.append(str(email))
            
            if invalid_emails:
                diagnosis["diagnosis"]["email_format"] = {
                    "status": "failed",
                    "message": f"收件人邮箱格式不正确: {', '.join(invalid_emails)}"
                }
                diagnosis["recommendations"].append(f"请检查收件人邮箱格式: {', '.join(invalid_emails)}")
                return jsonify(diagnosis)
        else:
            diagnosis["diagnosis"]["email_format"] = {
                "status": "failed",
                "message": "收件人邮箱配置格式错误"
            }
            diagnosis["recommendations"].append("收件人邮箱应为列表格式")
            return jsonify(diagnosis)

        diagnosis["diagnosis"]["email_format"] = {
            "status": "passed",
            "message": "邮箱格式检查通过"
        }

        # 4. SMTP连接诊断
        try:
            smtp_server = email_config["smtp_server"]
            smtp_port = email_config.get("smtp_port", 587)
            smtp_username = email_config["smtp_username"]
            smtp_password = email_config["smtp_password"]
            
            logger.info(f"邮件配置诊断: 开始SMTP连接测试 {smtp_server}:{smtp_port}")
            
            # 根据端口选择连接方式
            if smtp_port == 465:
                logger.info("诊断: 尝试SMTP_SSL连接")
                with smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=10) as server:
                    logger.info("诊断: SMTP_SSL连接成功")
                    server.login(smtp_username, smtp_password)
                    logger.info("诊断: SMTP_SSL登录成功")
                    
                    # 发送测试邮件
                    test_subject = "糖小助 - 邮件配置诊断测试"
                    test_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    clean_to_emails = [e.strip('"\'') for e in email_config['to_emails']]
                    test_content = f"""
这是一封诊断测试邮件，用于验证您的邮件配置是否正确。

📧 SMTP 服务器: {smtp_server}:{smtp_port}
🔐 连接类型: {'SSL' if smtp_port == 465 else 'STARTTLS'}
👤 发件人: {smtp_username}
📮 收件人: {', '.join(clean_to_emails)}

诊断测试时间: {test_time}

如果您收到这封邮件，说明邮件配置完全正常！
                    """
                    
                    # 创建邮件消息
                    msg = MIMEMultipart()
                    msg['From'] = email_config['from_email']
                    msg['To'] = ', '.join(clean_to_emails)
                    msg['Subject'] = test_subject
                    
                    # 添加邮件内容
                    msg.attach(MIMEText(test_content, 'plain', 'utf-8'))
                    
                    # 发送邮件
                    logger.info("诊断: 准备发送测试邮件...")
                    server.send_message(msg)
                    logger.info("诊断: 测试邮件发送成功")
                    
                    success = True
            else:
                logger.info("诊断: 尝试SMTP + STARTTLS连接")
                with smtplib.SMTP(smtp_server, smtp_port, timeout=10) as server:
                    logger.info("诊断: SMTP连接成功")
                    server.starttls()
                    logger.info("诊断: STARTTLS启用成功")
                    server.login(smtp_username, smtp_password)
                    logger.info("诊断: SMTP登录成功")
                    
                    # 发送测试邮件
                    test_subject = "糖小助 - 邮件配置诊断测试"
                    test_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    clean_to_emails = [e.strip('"\'') for e in email_config['to_emails']]
                    test_content = f"""
这是一封诊断测试邮件，用于验证您的邮件配置是否正确。

📧 SMTP 服务器: {smtp_server}:{smtp_port}
🔐 连接类型: {'SSL' if smtp_port == 465 else 'STARTTLS'}
👤 发件人: {smtp_username}
📮 收件人: {', '.join(clean_to_emails)}

诊断测试时间: {test_time}

如果您收到这封邮件，说明邮件配置完全正常！
                    """
                    
                    # 创建邮件消息
                    msg = MIMEMultipart()
                    msg['From'] = email_config['from_email']
                    msg['To'] = ', '.join(clean_to_emails)
                    msg['Subject'] = test_subject
                    
                    # 添加邮件内容
                    msg.attach(MIMEText(test_content, 'plain', 'utf-8'))
                    
                    # 发送邮件
                    logger.info("诊断: 准备发送测试邮件...")
                    server.send_message(msg)
                    logger.info("诊断: 测试邮件发送成功")
                    
                    success = True

            diagnosis["diagnosis"]["smtp_connection"] = {
                "status": "passed",
                "message": f"SMTP连接测试成功 ({smtp_server}:{smtp_port})"
            }

            # 5. 端口建议
            if smtp_port == 465:
                diagnosis["recommendations"].append("端口465使用SSL连接，配置正确")
            elif smtp_port == 587:
                diagnosis["recommendations"].append("端口587使用STARTTLS连接，配置正确")
            else:
                diagnosis["recommendations"].append(f"端口{smtp_port}非常见端口，请确认服务器支持")

            if success:
                diagnosis["diagnosis"]["email_delivery"] = {
                    "status": "passed",
                    "message": "测试邮件发送成功"
                }
                diagnosis["success"] = True
                diagnosis["recommendations"].append("🎉 邮件配置完全正常，可以正常使用！")
            else:
                diagnosis["diagnosis"]["email_delivery"] = {
                    "status": "failed",
                    "message": "测试邮件发送失败"
                }
                diagnosis["recommendations"].append("SMTP连接成功但邮件发送失败，请检查邮件内容或收件人设置")

        except smtplib.SMTPAuthenticationError:
            diagnosis["diagnosis"]["smtp_connection"] = {
                "status": "failed",
                "message": "SMTP认证失败"
            }
            diagnosis["recommendations"].append("用户名或密码错误，请检查凭据")
            diagnosis["recommendations"].append("部分邮件服务需要使用应用专用密码，而非登录密码")
        except smtplib.SMTPConnectError:
            diagnosis["diagnosis"]["smtp_connection"] = {
                "status": "failed",
                "message": "无法连接到SMTP服务器"
            }
            diagnosis["recommendations"].append("请检查服务器地址和端口是否正确")
            diagnosis["recommendations"].append("检查网络连接和防火墙设置")
        except Exception as e:
            diagnosis["diagnosis"]["smtp_connection"] = {
                "status": "failed",
                "message": f"连接测试失败: {str(e)}"
            }
            diagnosis["recommendations"].append(f"未知错误: {str(e)}")

        return jsonify(diagnosis)

    except Exception as e:
        logger.error(f"邮件配置诊断失败: {e}")
        return jsonify({
            "success": False,
            "error": f"诊断失败: {str(e)}"
        })

@app.route('/api/test-ai', methods=['POST'])
def api_test_ai():
    """测试AI连接配置"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({
                "success": False,
                "error": "请求参数缺失"
            })
        
        api_url = data.get('api_url', '').strip()
        api_key = data.get('api_key', '').strip()
        model_name = data.get('model_name', '').strip()
        timeout = data.get('timeout', 30)
        
        if not api_url:
            return jsonify({
                "success": False,
                "error": "API 地址不能为空"
            })
        
        if not model_name:
            return jsonify({
                "success": False,
                "error": "模型名称不能为空"
            })
        
        # 创建测试请求数据
        request_data = {
            "model": model_name,
            "messages": [
                {
                    "role": "user",
                    "content": "请回复'连接测试成功'来验证AI服务正常工作。"
                }
            ],
            "max_tokens": 50,
            "temperature": 0.7
        }
        
        # 设置请求头
        headers = {
            "Content-Type": "application/json"
        }
        
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        
        # 测试连接
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            result = loop.run_until_complete(
                test_ai_connection_async(api_url, headers, request_data, timeout)
            )
            return jsonify(result)
        finally:
            loop.close()
            
    except Exception as e:
        return jsonify({
            "success": False,
            "error": f"测试失败: {str(e)}"
        })

@ai_retry_decorator(max_retries=3)
async def _make_ai_connection_request(api_url: str, headers: dict, request_data: dict, timeout: int) -> dict:
    """执行AI连接测试HTTP请求（带有重试机制）"""
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        async with session.post(api_url, json=request_data, headers=headers) as response:
            if response.status == 200:
                result = await response.json()
                
                # 验证响应格式
                if 'choices' in result and len(result['choices']) > 0:
                    choice = result['choices'][0]
                    if 'message' in choice and 'content' in choice['message']:
                        ai_response = choice['message']['content'].strip()
                        
                        # 验证AI响应内容
                        if ai_response:
                            return {
                                "success": True,
                                "message": f"AI连接正常，模型响应: {ai_response[:50]}{'...' if len(ai_response) > 50 else ''}"
                            }
                        else:
                            raise ValueError("AI响应内容为空")
                    else:
                        raise ValueError("AI响应格式错误，缺少message或content字段")
                else:
                    raise ValueError("AI响应格式错误，缺少choices字段")
            else:
                error_text = await response.text()
                raise Exception(f"HTTP {response.status}: {error_text[:200]}")

async def test_ai_connection_async(api_url: str, headers: dict, request_data: dict, timeout: int) -> dict:
    """异步测试AI连接"""
    try:
        result = await _make_ai_connection_request(api_url, headers, request_data, timeout)
        return result
            
    except asyncio.TimeoutError:
        return {
            "success": False,
            "error": f"连接超时（{timeout}秒），请检查网络或增加超时时间"
        }
    except aiohttp.ClientError as e:
        return {
            "success": False,
            "error": f"网络连接错误: {str(e)}"
        }
    except json.JSONDecodeError as e:
        return {
            "success": False,
            "error": f"JSON解析错误: {str(e)}"
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"未知错误: {str(e)}"
        }

def get_glucose_color_class(value):
    if value is None:
        return ''
    try:
        val = float(value)
        if val < 3.9:
            return 'text-warning'
        elif val > 7.8:
            return 'text-danger'
        else:
            return 'text-success'
    except (ValueError, TypeError):
        return ''

@app.context_processor
def utility_processor():
    return dict(get_glucose_color_class=get_glucose_color_class)

@app.route('/report')
def report_page():
    """报表页面"""
    try:
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        exclude_times_param = request.args.get('exclude_times')
        exclude_times = None
        
        # 解析排除时间段参数
        if exclude_times_param:
            try:
                exclude_times = json.loads(exclude_times_param)
                if not isinstance(exclude_times, list):
                    exclude_times = None
            except (json.JSONDecodeError, TypeError):
                logger.warning(f"解析排除时间段参数失败: {exclude_times_param}")
                exclude_times = None
        
        if not start_date or not end_date:
            # 默认显示最近7天
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=6)).strftime('%Y-%m-%d')
        
        # 验证日期格式
        try:
            datetime.strptime(start_date, '%Y-%m-%d')
            datetime.strptime(end_date, '%Y-%m-%d')
        except ValueError:
            # 如果日期格式无效，使用默认7天
            end_date = datetime.now().strftime('%Y-%m-%d')
            start_date = (datetime.now() - timedelta(days=6)).strftime('%Y-%m-%d')
        
        # 限制最大日期范围为365天，防止性能问题
        start_dt = datetime.strptime(start_date, '%Y-%m-%d')
        end_dt = datetime.strptime(end_date, '%Y-%m-%d')
        date_diff = (end_dt - start_dt).days
        
        if date_diff > 365:
            start_date = (end_dt - timedelta(days=365)).strftime('%Y-%m-%d')
        elif date_diff < 0:
            # 如果开始日期晚于结束日期，交换它们
            start_date, end_date = end_date, start_date
        
        # 生成报表数据
        report_data = monitor.generate_report_data(start_date, end_date, exclude_times)
        
        # 准备模板上下文
        context = {
            'start_date': start_date,
            'end_date': end_date,
            'generation_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'summary': report_data.get('summary', {}),
            'daily_data': report_data.get('daily_data', []),
            'config': monitor.config
        }
        
        return render_template('report.html', **context)
        
    except Exception as e:
        logger.error(f"报表页面加载失败: {e}")
        # 返回空数据的报表页面
        context = {
            'start_date': start_date or '',
            'end_date': end_date or '',
            'generation_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'summary': {},
            'daily_data': []
        }
        
        # 添加配置数据到上下文
        context['config'] = monitor.config
        
        return render_template('report.html', **context)

@app.route('/api/report-data')
def api_report_data():
    """获取报表数据API"""
    try:
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        exclude_times_param = request.args.get('exclude_times')
        exclude_times = None
        
        # 解析排除时间段参数
        if exclude_times_param:
            try:
                exclude_times = json.loads(exclude_times_param)
                if not isinstance(exclude_times, list):
                    exclude_times = None
            except (json.JSONDecodeError, TypeError):
                logger.warning(f"解析排除时间段参数失败: {exclude_times_param}")
                exclude_times = None
        
        if not start_date or not end_date:
            return jsonify({'error': '缺少日期参数'}), 400
            
        # 验证日期格式
        try:
            datetime.strptime(start_date, '%Y-%m-%d')
            datetime.strptime(end_date, '%Y-%m-%d')
        except ValueError:
            return jsonify({'error': '日期格式错误，请使用YYYY-MM-DD格式'}), 400
            
        # 生成报表数据
        report_data = monitor.generate_report_data(start_date, end_date, exclude_times)
        
        if 'error' in report_data and report_data['error']:
            return jsonify({'error': report_data['error']}), 404
            
        return jsonify(report_data)
        
    except Exception as e:
        logger.error(f"获取报表数据失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/messages', methods=['GET'])
def api_get_messages():
    """获取消息列表API"""
    try:
        message_type = request.args.get('type')
        limit = request.args.get('limit', 50, type=int)
        
        messages = monitor.get_messages(message_type, limit)
        return jsonify({'messages': messages})
    except Exception as e:
        logger.error(f"获取消息列表失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/messages/<int:message_id>', methods=['PUT'])
def api_update_message(message_id):
    """更新消息状态API"""
    try:
        data = request.get_json()
        is_read = data.get('is_read') if 'is_read' in data else None
        is_favorite = data.get('is_favorite') if 'is_favorite' in data else None
        
        if is_read is None and is_favorite is None:
            return jsonify({'error': '缺少更新参数'}), 400
        
        success = monitor.update_message_status(message_id, is_read, is_favorite)
        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'error': '更新失败'}), 500
    except Exception as e:
        logger.error(f"更新消息状态失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/messages/<int:message_id>', methods=['DELETE'])
def api_delete_message(message_id):
    """删除消息API"""
    try:
        success = monitor.delete_message(message_id)
        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'error': '删除失败'}), 500
    except Exception as e:
        logger.error(f"删除消息失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/messages/batch', methods=['DELETE'])
def api_delete_messages_batch():
    """批量删除消息API"""
    try:
        data = request.get_json()
        message_ids = data.get('message_ids', [])
        
        if not message_ids or not isinstance(message_ids, list):
            return jsonify({'error': '缺少有效的消息ID列表'}), 400
        
        success = monitor.delete_messages_batch(message_ids)
        if success:
            return jsonify({
                'success': True, 
                'deleted_count': len(message_ids),
                'message': f'成功删除 {len(message_ids)} 条消息'
            })
        else:
            return jsonify({'error': '批量删除失败'}), 500
    except Exception as e:
        logger.error(f"批量删除消息失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/messages/unread-count', methods=['GET'])
def api_unread_count():
    """获取未读消息数量API"""
    try:
        count = monitor.get_unread_message_count()
        return jsonify({'unread_count': count})
    except Exception as e:
        logger.error(f"获取未读消息数量失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/predict', methods=['GET'])
def api_predict_glucose():
    """血糖预测API"""
    try:
        # 获取请求参数
        force_current = request.args.get('force_current', 'false').lower() == 'true'
        
        # 获取最近7天的血糖数据用于预测
        glucose_data = monitor.get_glucose_data_from_db(days=7)
        
        # 获取最近7天的治疗数据用于增强预测
        treatment_data = monitor.get_treatment_data_from_db(days=7)
        
        # 检查用户配置是否启用了预测
        config = monitor.get_user_alert_config()
        if not config.get('enable_predictions', True):
            return jsonify({'error': '血糖预测功能已禁用'}), 400
        
        # 执行预测（包含治疗数据增强）
        if force_current:
            logger.info("收到强制重新预测请求，使用当前值为基础预测")
        prediction_result = monitor.predict_glucose(glucose_data, treatment_data, force_current_based=force_current)
        
        # 保存预测结果
        monitor.save_prediction_result(prediction_result)
        
        # 评估低血糖风险
        risk_assessment = monitor.assess_hypoglycemia_risk(prediction_result['predicted_glucose_mgdl'])
        
        # 如果启用了警报且风险不为LOW，创建警报
        if config.get('enable_alerts', True) and risk_assessment['risk_level'] != 'LOW':
            alert_id = monitor.create_hypoglycemia_alert(risk_assessment)
            if alert_id > 0:
                # 发送邮件通知
                monitor.send_glucose_alert_notification(risk_assessment, alert_id)
        
        return jsonify({
            'prediction': prediction_result,
            'risk_assessment': risk_assessment,
            'status': 'success'
        })
        
    except ValueError as ve:
        logger.warning(f"血糖预测数据不足: {ve}")
        return jsonify({'error': str(ve)}), 400
    except Exception as e:
        logger.error(f"血糖预测失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/alerts/config', methods=['GET', 'POST'])
def api_alerts_config():
    """获取或更新警报配置API"""
    try:
        if request.method == 'GET':
            config = monitor.get_user_alert_config()
            return jsonify(config)
        
        elif request.method == 'POST':
            data = request.get_json()
            
            # 验证输入数据
            if not data:
                return jsonify({'error': '请求数据不能为空'}), 400
            
            # 验证阈值
            high_threshold = data.get('high_risk_threshold_mgdl', 70)
            medium_threshold = data.get('medium_risk_threshold_mgdl', 80)
            
            if high_threshold <= 0 or medium_threshold <= 0:
                return jsonify({'error': '阈值必须大于0'}), 400
            
            if high_threshold >= medium_threshold:
                return jsonify({'error': '高风险阈值必须小于中等风险阈值'}), 400
            
            # 更新配置
            success = monitor.update_user_alert_config(data)
            
            if success:
                return jsonify({'success': True, 'message': '警报配置已更新'})
            else:
                return jsonify({'error': '配置更新失败'}), 500
                
    except Exception as e:
        logger.error(f"警报配置操作失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/alerts/history', methods=['GET'])
def api_alerts_history():
    """获取警报历史API"""
    try:
        limit = request.args.get('limit', 50, type=int)
        alerts = monitor.get_alert_history(limit)
        
        return jsonify({
            'alerts': alerts,
            'total_count': len(alerts)
        })
        
    except Exception as e:
        logger.error(f"获取警报历史失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/alerts/acknowledge', methods=['POST'])
def api_alerts_acknowledge():
    """确认警报API"""
    try:
        data = request.get_json()
        if not data or 'alert_id' not in data:
            return jsonify({'error': '缺少alert_id参数'}), 400
        
        alert_id = data['alert_id']
        
        if not isinstance(alert_id, int) or alert_id <= 0:
            return jsonify({'error': 'alert_id必须是正整数'}), 400
        
        success = monitor.acknowledge_alert(alert_id)
        
        if success:
            return jsonify({'success': True, 'message': '警报已确认'})
        else:
            return jsonify({'error': '警报确认失败，可能已不存在或已被确认'}), 404
            
    except Exception as e:
        logger.error(f"确认警报失败: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/login', methods=['GET', 'POST'])
def login():
    """登录页面"""
    if not monitor.config.get('auth', {}).get('enable'):
        return redirect(url_for('index'))

    if request.method == 'POST':
        password = request.form.get('password')
        remember = request.form.get('remember')

        if password == monitor.config.get('auth', {}).get('password'):
            session['logged_in'] = True
            if remember:
                session.permanent = True
                app.permanent_session_lifetime = timedelta(days=30)
            else:
                session.permanent = False
            
            next_url = request.args.get('next')
            return redirect(next_url or url_for('index'))
        else:
            flash('密码错误，请重试', 'danger')

    return render_template('login.html')

@app.route('/logout')
def logout():
    """登出"""
    session.pop('logged_in', None)
    flash('您已成功登出', 'success')
    return redirect(url_for('login'))

# SocketIO 事件处理
@socketio.on('connect')
def handle_connect():
    """客户端连接事件"""
    logger.info('客户端已连接')

@socketio.on('disconnect')
def handle_disconnect():
    """客户端断开连接事件"""
    logger.info('客户端已断开连接')

@socketio.on('subscribe_notifications')
def handle_subscribe_notifications(data):
    """订阅通知事件"""
    try:
        # 这里可以保存客户端的推送订阅信息
        logger.info('客户端订阅了通知')
        emit('subscription_confirmed', {'status': 'success'})
    except Exception as e:
        logger.error(f"订阅通知失败: {e}")
        emit('subscription_confirmed', {'status': 'error', 'message': str(e)})


if __name__ == '__main__':
    # 配置日志
    logger.add("logs/nightscout_web.log", rotation="1 day", retention="30 days")
    logger.info("糖小助启动中...")

    # 创建必要的目录
    os.makedirs("logs", exist_ok=True)
    os.makedirs("static", exist_ok=True)
    os.makedirs("templates", exist_ok=True)

    # 启动应用
    port = int(os.environ.get('PORT', 1338))
    debug = os.environ.get('DEBUG', 'False').lower() == 'true'

    logger.info(f"应用将在端口 {port} 启动")
    socketio.run(app, host='0.0.0.0', port=port, debug=debug)

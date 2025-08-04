#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
糖小助 - 独立的血糖监控Web应用
"""

import asyncio
import json
import os
import sqlite3
import time
try:
    import tomllib
except ImportError:
    import tomli as tomllib
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
import threading
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formatdate

import aiohttp
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session
from flask_socketio import SocketIO, emit
from loguru import logger
import schedule as schedule_lib

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'your-secret-key-change-this')
socketio = SocketIO(app, cors_allowed_origins="*")

class NightscoutWebMonitor:
    def __init__(self):
        self.config = self.load_config()
        self.init_database()
        self.setup_scheduler()
        
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
            }
        }
        
        try:
            if os.path.exists(config_path):
                with open(config_path, "rb") as f:
                    config = tomllib.load(f)
                # 合并默认配置
                for section, values in default_config.items():
                    if section not in config:
                        config[section] = values
                    else:
                        for key, value in values.items():
                            if key not in config[section]:
                                config[section][key] = value
                return config
            else:
                return default_config
        except Exception as e:
            logger.error(f"加载配置文件失败: {e}")
            return default_config

    def save_config(self, config):
        """保存配置文件"""
        try:
            import toml
            with open("config.toml", "w", encoding="utf-8") as f:
                toml.dump(config, f)
            self.config = config
            return True
        except Exception as e:
            logger.error(f"保存配置文件失败: {e}")
            return False

    def init_database(self):
        """初始化数据库"""
        try:
            conn = sqlite3.connect("nightscout_data.db")
            cursor = conn.cursor()
            
            # 创建血糖数据表
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

            # 创建治疗数据表
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

            # 创建运动数据表
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

            # 创建指尖血糖数据表
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
        """将UTC时间字符串转换为上海时间字符串"""
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

            shanghai_tz = timezone(timedelta(hours=8))
            shanghai_dt = utc_dt.astimezone(shanghai_tz)
            return shanghai_dt.strftime('%Y-%m-%d %H:%M:%S')

        except Exception as e:
            logger.error(f"时区转换失败: {utc_time_str}, 错误: {e}")
            return utc_time_str

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
                        'created_at': item.get('created_at', ''),
                        'eventType': event_type or '运动',
                        'duration': item.get('duration', 0),
                        'notes': item.get('notes', '')
                    })
                
                # 识别指尖血糖数据（BG Check事件中的glucose值已经是mmol/L单位）
                if event_type == 'BG Check':
                    glucose_value = item.get('glucose', 0)
                    # 确保数值是合理的mmol/L范围
                    if glucose_value and float(glucose_value) > 0:
                        filtered_meter_data.append({
                            'dateString': item.get('created_at', ''),
                            'sgv': float(glucose_value)
                        })

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
        today = datetime.now().strftime('%Y-%m-%d')
        glucose_data, treatment_data, activity_data, meter_data = await self.fetch_nightscout_data(today, today)
        
        if glucose_data:
            await self.save_glucose_data(glucose_data)
        if treatment_data:
            await self.save_treatment_data(treatment_data)
        if activity_data:
            await self.save_activity_data(activity_data)
        if meter_data:
            await self.save_meter_data(meter_data)

        if glucose_data:
            analysis = await self.get_ai_analysis(glucose_data, treatment_data, activity_data, meter_data, 1)
            
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
        end_date = datetime.now().strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(hours=2)).strftime('%Y-%m-%d')
        
        glucose_data, treatment_data, activity_data, meter_data = await self.fetch_nightscout_data(start_date, end_date)
        
        if glucose_data:
            await self.save_glucose_data(glucose_data)
        if treatment_data:
            await self.save_treatment_data(treatment_data)
        if activity_data:
            await self.save_activity_data(activity_data)
        if meter_data:
            await self.save_meter_data(meter_data)

    async def save_glucose_data(self, glucose_data: List[Dict]):
        """保存血糖数据到数据库"""
        try:
            conn = sqlite3.connect("nightscout_data.db")
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
            conn.close()
            logger.info(f"保存了 {saved_count} 条新的血糖数据")

        except Exception as e:
            logger.error(f"保存血糖数据失败: {e}")

    async def save_treatment_data(self, treatment_data: List[Dict]):
        """保存治疗数据到数据库"""
        try:
            conn = sqlite3.connect("nightscout_data.db")
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
            conn = sqlite3.connect("nightscout_data.db")
            cursor = conn.cursor()

            saved_count = 0
            for entry in activity_data:
                try:
                    utc_time = entry.get("created_at") or entry.get("timestamp") or ""
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
            conn = sqlite3.connect("nightscout_data.db")
            cursor = conn.cursor()

            saved_count = 0
            for entry in meter_data:
                try:
                    utc_time = entry.get("dateString", "")
                    shanghai_time = self.utc_to_shanghai_time(utc_time)

                    cursor.execute("""
                        INSERT OR IGNORE INTO meter_data
                        (date_string, shanghai_time, sgv)
                        VALUES (?, ?, ?)
                    """, (
                        entry.get("dateString"),
                        shanghai_time,
                        entry.get("sgv")
                    ))

                    if cursor.rowcount > 0:
                        saved_count += 1

                except Exception as e:
                    logger.error(f"保存指尖血糖数据项失败: {e}")

            conn.commit()
            conn.close()
            logger.info(f"保存了 {saved_count} 条新的指尖血糖数据")

        except Exception as e:
            logger.error(f"保存指尖血糖数据失败: {e}")

    def get_glucose_data_from_db(self, days: int = 7, start_date: Optional[str] = None, end_date: Optional[str] = None) -> List[Dict]:
        """从数据库获取血糖数据"""
        try:
            conn = sqlite3.connect("nightscout_data.db")
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
                start_date_str = (datetime.now() - timedelta(days=days-1)).strftime('%Y-%m-%d')
                query = """
                    SELECT date_string, shanghai_time, sgv, direction, trend
                    FROM glucose_data
                    WHERE shanghai_time >= ?
                    ORDER BY date_string DESC
                """
                params = (start_date_str,)

            cursor.execute(query, params)
            rows = cursor.fetchall()
            conn.close()

            glucose_data = []
            for row in rows:
                glucose_data.append({
                    "dateString": row[0],
                    "shanghai_time": row[1],
                    "sgv": row[2],
                    "direction": row[3],
                    "trend": row[4]
                })

            return glucose_data

        except Exception as e:
            logger.error(f"从数据库获取血糖数据失败: {e}")
            return []

    def get_treatment_data_from_db(self, days: int = 7, start_date: Optional[str] = None, end_date: Optional[str] = None) -> List[Dict]:
        """从数据库获取治疗数据"""
        try:
            conn = sqlite3.connect("nightscout_data.db")
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
                start_date_str = (datetime.now() - timedelta(days=days-1)).strftime('%Y-%m-%d')
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
            conn = sqlite3.connect("nightscout_data.db")
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
                start_date_str = (datetime.now() - timedelta(days=days-1)).strftime('%Y-%m-%d')
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
            conn = sqlite3.connect("nightscout_data.db")
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
                start_date_str = (datetime.now() - timedelta(days=days-1)).strftime('%Y-%m-%d')
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
                    "dateString": row[0],
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
            conn = sqlite3.connect("nightscout_data.db")
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
    
    def get_messages(self, message_type: Optional[str] = None, limit: int = 50) -> List[Dict]:
        """从数据库获取消息"""
        try:
            conn = sqlite3.connect("nightscout_data.db")
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
                    'created_at': row[6]
                })
            
            return messages
        except Exception as e:
            logger.error(f"获取消息失败: {e}")
            return []
    
    def update_message_status(self, message_id: int, is_read: Optional[bool] = None, is_favorite: Optional[bool] = None) -> bool:
        """更新消息状态"""
        try:
            conn = sqlite3.connect("nightscout_data.db")
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
            conn = sqlite3.connect("nightscout_data.db")
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
    
    def get_unread_message_count(self) -> int:
        """获取未读消息数量"""
        try:
            conn = sqlite3.connect("nightscout_data.db")
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

    async def get_ai_analysis(self, glucose_data: List[Dict], treatment_data: List[Dict], activity_data: List[Dict], meter_data: List[Dict], days: int = 1) -> str:
        """获取AI分析结果"""
        try:
            prompt = self.get_analysis_prompt(glucose_data, treatment_data, activity_data, meter_data, days)

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
                "max_tokens": 800,
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
                            logger.error(f"AI响应格式错误: {result}")
                            return "AI服务暂时不可用，请稍后再试。"
                    else:
                        logger.error(f"AI请求失败: {response.status}")
                        return "AI服务暂时不可用，请稍后再试。"

        except Exception as e:
            logger.error(f"获取AI分析失败: {e}")
            return "AI服务暂时不可用，建议咨询专业医生获得详细指导。"

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

    def get_analysis_prompt(self, glucose_data: List[Dict], treatment_data: List[Dict], activity_data: List[Dict], meter_data: List[Dict], days: int = 1) -> str:
        """生成AI分析的prompt"""

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

        for entry in treatment_data:
            carbs = entry.get("carbs")
            protein = entry.get("protein")
            fat = entry.get("fat")

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

                meals.append({
                    "time": shanghai_time,
                    "carbs": carbs,
                    "protein": protein_value,
                    "fat": fat_value,
                    "notes": entry.get("notes", ""),
                    "event_type": entry.get("eventType", "")
                })

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
        
        personal_info = []
        if bmi_data.get("bmi") > 0:
            personal_info.append(f"用户BMI为 {bmi_data['bmi']} ({bmi_data['status']})")
        if body_fat > 0:
            personal_info.append(f"体脂率为 {body_fat}%")
        
        prompt_info = " ".join(personal_info)

        prompt = f"""你是一位专业的内分泌科医生和糖尿病管理专家。请分析以下{days}天的血糖监测数据，并提供专业的医学建议。{prompt_info}

血糖数据（mmol/L）：
"""

        # 添加血糖数据
        for entry in glucose_mmol[:20]:
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

        # 添加指尖血糖数据
        if meter_mmol:
            prompt += f"\n指尖血糖数据（mmol/L）：\n"
            for entry in meter_mmol[:10]:
                prompt += f"• {entry['time']}: {entry['value']} mmol/L\n"

        if meals:
            prompt += f"\n餐食记录（总碳水: {carbs_total}g, 总蛋白质: {protein_total}g, 总脂肪: {fat_total}g）：\n"

            for meal in meals[:10]:
                event_info = f"[{meal['event_type']}]" if meal['event_type'] else ""
                notes_info = f" - {meal['notes']}" if meal['notes'] else ""

                nutrition_parts = [f"{meal['carbs']}g碳水"]
                if meal['protein'] > 0:
                    nutrition_parts.append(f"{meal['protein']}g蛋白质")
                if meal['fat'] > 0:
                    nutrition_parts.append(f"{meal['fat']}g脂肪")
                nutrition_info = ", ".join(nutrition_parts)

                prompt += f"• {meal['time']}: {nutrition_info} {event_info}{notes_info}\n"
        else:
            prompt += f"\n餐食记录：无碳水摄入记录\n"

        # 添加运动数据
        if activities:
            prompt += f"\n运动记录（总时长: {total_duration}分钟）：\n"
            for activity in activities[:10]:
                event_info = f"[{activity['event_type']}]" if activity['event_type'] else ""
                notes_info = f" - {activity['notes']}" if activity['notes'] else ""
                prompt += f"• {activity['time']}: {activity['duration']}分钟 {event_info}{notes_info}\n"
        else:
            prompt += f"\n运动记录：无运动记录\n"

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
• 运动记录：{len(activities)}次

请提供以下分析：
1. 血糖控制状况评估
2. 血糖波动模式分析
3. 餐后血糖反应评估
4. 营养摄入分析
5. 运动对血糖的影响分析
6. 具体的改善建议
7. 需要关注的风险点

请用专业但易懂的语言回答，控制在400字以内。"""

        return prompt

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

            prompt = self.get_consultation_prompt(question, glucose_data, treatment_data, activity_data, meter_data, days, include_data)

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
                "max_tokens": 500,
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
                            logger.error(f"AI响应格式错误: {result}")
                            return "AI服务暂时不可用，请稍后再试。"
                    else:
                        logger.error(f"AI请求失败: {response.status}")
                        return "AI服务暂时不可用，请稍后再试。"

        except Exception as e:
            logger.error(f"获取AI咨询失败: {e}")
            return "AI服务暂时不可用，建议咨询专业医生获得详细指导。"

    def get_consultation_prompt(self, question: str, glucose_data: List[Dict], treatment_data: List[Dict], activity_data: List[Dict], meter_data: List[Dict], days: int, include_data: bool) -> str:
        """生成AI咨询的prompt"""
        bmi_data = self.calculate_bmi()
        body_fat = self.config.get("basic", {}).get("body_fat_percentage", 0)

        personal_info = []
        if bmi_data.get("bmi") > 0:
            personal_info.append(f"用户BMI为 {bmi_data['bmi']} ({bmi_data['status']})")
        if body_fat > 0:
            personal_info.append(f"体脂率为 {body_fat}%")
            
        prompt_info = " ".join(personal_info)

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

            prompt = f"""你是一位专业的内分泌科医生和糖尿病管理专家。请根据以下最近{days}天的血糖数据，回答用户的问题。{prompt_info}

血糖数据（mmol/L, 最近20条）:
"""
            for entry in glucose_mmol[:20]:
                prompt += f"• {entry['time']}: {entry['value']} mmol/L\n"

            if meter_mmol:
                prompt += f"\n指尖血糖数据（mmol/L, 最近10条）:\n"
                for entry in meter_mmol[:10]:
                    prompt += f"• {entry['time']}: {entry['value']} mmol/L\n"

            if activities:
                prompt += f"\n运动数据（最近10条）:\n"
                for activity in activities[:10]:
                    event_info = f"[{activity['event_type']}]" if activity['event_type'] else ""
                    notes_info = f" - {activity['notes']}" if activity['notes'] else ""
                    prompt += f"• {activity['time']}: {activity['duration']}分钟 {event_info}{notes_info}\n"

            prompt += f"""
用户问题: "{question}"

请用专业、简洁、易懂的语言回答，并提供可行的建议。如果数据不足以回答问题，请明确指出。
"""
        else:
            prompt = f"""你是一位专业的内分泌科医生和糖尿病管理专家。请回答以下用户的问题。{prompt_info}

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
        try:
            if not self.config.get("notification", {}).get("enable_email", False):
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

            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = email_config["from_email"]
            msg['To'] = ", ".join(email_config["to_emails"])
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

            # 发送邮件
            with smtplib.SMTP(email_config["smtp_server"], email_config.get("smtp_port", 587)) as server:
                server.starttls()
                server.login(email_config["smtp_username"], email_config["smtp_password"])
                server.send_message(msg)

            logger.info(f"邮件发送成功: {subject}")
            return True

        except Exception as e:
            logger.error(f"邮件发送失败: {e}")
            return False

    def create_email_html_template(self, subject: str, content: str) -> str:
        """创建邮件HTML模板"""
        from datetime import datetime

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
                    生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
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

            # 测试SMTP连接
            with smtplib.SMTP(email_config["smtp_server"], email_config.get("smtp_port", 587)) as server:
                server.starttls()
                server.login(email_config["smtp_username"], email_config["smtp_password"])

                # 发送测试邮件
                test_subject = "糖小助 - 邮件配置测试"
                test_content = f"""
这是一封测试邮件，用于验证您的邮件配置是否正确。

📧 SMTP 服务器: {email_config['smtp_server']}:{email_config.get('smtp_port', 587)}
👤 发件人: {email_config['from_email']}
📮 收件人: {', '.join(email_config['to_emails'])}

如果您收到这封邮件，说明邮件配置已经成功！

测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                """

                success = self.send_email_notification(test_subject, test_content)

                if success:
                    return {
                        "success": True,
                        "message": "邮件配置测试成功！测试邮件已发送"
                    }
                else:
                    return {
                        "success": False,
                        "error": "邮件发送失败，请检查配置"
                    }

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
        except Exception as e:
            return {
                "success": False,
                "error": f"邮件配置测试失败: {str(e)}"
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
                    
                    # 早餐后：早餐后2小时（8-10点）
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
                        # 查找当天的午餐记录（11-13点之间有碳水摄入的记录）
                        lunch_meals = [m for m in meals_by_date[date_str] if 11 <= m['hour'] < 13]
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
                    
                    # 晚餐后：晚餐后2小时（18-20点）
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
                    
                    # 早餐后对应的指尖血糖（8-10点）
                    breakfast_after_meter = next((m['value'] for m in day_meter if 8 <= m['hour'] < 10), None)
                    day_data['breakfast_after_meter'] = breakfast_after_meter
                    
                    # 午餐前对应的指尖血糖（11-12点）
                    lunch_before_meter = next((m['value'] for m in day_meter if 11 <= m['hour'] < 12), None)
                    day_data['lunch_before_meter'] = lunch_before_meter
                    
                    # 午餐后对应的指尖血糖（基于实际餐食时间或12-14点）
                    lunch_after_meter = None
                    if date_str in meals_by_date:
                        # 查找当天的午餐记录（11-13点之间有碳水摄入的记录）
                        lunch_meals = [m for m in meals_by_date[date_str] if 11 <= m['hour'] < 13]
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
                    
                    # 晚餐后对应的指尖血糖（18-20点）
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
    return render_template('config.html', config=monitor.config)

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
    """获取指尖血糖数据API"""
    days = request.args.get('days', 7, type=int)
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    meter_data = monitor.get_meter_data_from_db(days=days, start_date=start_date, end_date=end_date)

    # 转换数据格式用于前端显示
    formatted_data = []
    try:
        conn = sqlite3.connect("nightscout_data.db")
        cursor = conn.cursor()

        for entry in meter_data:
            # 获取指尖血糖的时间
            meter_time_str = entry.get('dateString', '')
            if not meter_time_str:
                continue
            
            # 寻找最接近的CGM血糖值
            # 使用 julianday 函数来计算时间差
            query = """
                SELECT sgv
                FROM glucose_data
                ORDER BY ABS(julianday(?) - julianday(date_string))
                LIMIT 1
            """
            cursor.execute(query, (meter_time_str,))
            closest_cgm_row = cursor.fetchone()
            
            cgm_sgv = closest_cgm_row[0] if closest_cgm_row else None
            cgm_mmol = monitor.mg_dl_to_mmol_l(cgm_sgv) if cgm_sgv is not None else None

            # 指尖血糖数据已经是mmol/L单位，无需转换
            formatted_data.append({
                'time': entry.get('shanghai_time', ''),
                'value_mmol': float(entry.get('sgv', 0)),  # 直接使用mmol/L
                'cgm_value_mmol': cgm_mmol
            })
    except Exception as e:
        logger.error(f"处理指尖血糖数据时出错: {e}")
    finally:
        if 'conn' in locals() and conn:
            conn.close()

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

@app.route('/api/analysis')
def api_analysis():
    """获取AI分析API"""
    days = request.args.get('days', 1, type=int)

    glucose_data = monitor.get_glucose_data_from_db(days)
    treatment_data = monitor.get_treatment_data_from_db(days)
    activity_data = monitor.get_activity_data_from_db(days)
    meter_data = monitor.get_meter_data_from_db(days)

    if not glucose_data:
        return jsonify({'error': '暂无血糖数据'}), 404

    try:
        try:
            analysis = asyncio.run(monitor.get_ai_analysis(glucose_data, treatment_data, activity_data, meter_data, days))
            # 保存分析结果到消息表
            monitor.save_message("analysis", "血糖分析报告", analysis)
            return jsonify({'analysis': analysis})
        except RuntimeError as e:
            # 处理在非主线程中运行asyncio.run可能出现的问题
            if "cannot run loop while another loop is running" in str(e):
                loop = asyncio.get_event_loop()
                analysis = loop.run_until_complete(monitor.get_ai_analysis(glucose_data, treatment_data, activity_data, meter_data, days))
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
            'daily_data': report_data.get('daily_data', [])
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

@app.route('/api/messages/unread-count', methods=['GET'])
def api_unread_count():
    """获取未读消息数量API"""
    try:
        count = monitor.get_unread_message_count()
        return jsonify({'unread_count': count})
    except Exception as e:
        logger.error(f"获取未读消息数量失败: {e}")
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
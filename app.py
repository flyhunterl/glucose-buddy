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
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash
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

    async def fetch_nightscout_data(self, start_date: str, end_date: str) -> Tuple[List[Dict], List[Dict]]:
        """从Nightscout获取指定时间范围的数据"""
        try:
            entries_url = f"{self.config['nightscout']['api_url']}/api/v1/entries.json"
            treatments_url = f"{self.config['nightscout']['api_url']}/api/v1/treatments.json"

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

            return glucose_data, treatment_data

        except Exception as e:
            logger.error(f"获取Nightscout数据失败: {e}")
            return [], []

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
        glucose_data, treatment_data = await self.fetch_nightscout_data(today, today)
        
        if glucose_data:
            await self.save_glucose_data(glucose_data)
            await self.save_treatment_data(treatment_data)
            
            analysis = await self.get_ai_analysis(glucose_data, treatment_data, 1)
            
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
        
        glucose_data, treatment_data = await self.fetch_nightscout_data(start_date, end_date)
        
        if glucose_data:
            await self.save_glucose_data(glucose_data)
        if treatment_data:
            await self.save_treatment_data(treatment_data)

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

    async def get_ai_analysis(self, glucose_data: List[Dict], treatment_data: List[Dict], days: int = 1) -> str:
        """获取AI分析结果"""
        try:
            prompt = self.get_analysis_prompt(glucose_data, treatment_data, days)

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

    def get_analysis_prompt(self, glucose_data: List[Dict], treatment_data: List[Dict], days: int = 1) -> str:
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

        bmi_data = self.calculate_bmi()
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

请提供以下分析：
1. 血糖控制状况评估
2. 血糖波动模式分析
3. 餐后血糖反应评估
4. 营养摄入分析
5. 具体的改善建议
6. 需要关注的风险点

请用专业但易懂的语言回答，控制在400字以内。"""

        return prompt

    async def get_ai_consultation(self, question: str, include_data: bool, days: int = 1) -> str:
        """获取AI咨询结果"""
        try:
            glucose_data = []
            treatment_data = []

            if include_data:
                glucose_data = self.get_glucose_data_from_db(days)
                treatment_data = self.get_treatment_data_from_db(days)

                if not glucose_data:
                    return "抱歉，没有足够的血糖数据来进行咨询。请先同步数据。"

            prompt = self.get_consultation_prompt(question, glucose_data, treatment_data, days, include_data)

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

    def get_consultation_prompt(self, question: str, glucose_data: List[Dict], treatment_data: List[Dict], days: int, include_data: bool) -> str:
        """生成AI咨询的prompt"""
        bmi_data = self.calculate_bmi()
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

            prompt = f"""你是一位专业的内分泌科医生和糖尿病管理专家。请根据以下最近{days}天的血糖数据，回答用户的问题。{prompt_info}

血糖数据（mmol/L, 最近20条）:
"""
            for entry in glucose_mmol[:20]:
                prompt += f"• {entry['time']}: {entry['value']} mmol/L\n"

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

# 创建全局实例
monitor = NightscoutWebMonitor()

# Flask 路由
@app.route('/')
def index():
    """主页 - 显示血糖数据表格"""
    return render_template('index.html')

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

    if not glucose_data:
        return jsonify({'error': '暂无血糖数据'}), 404

    try:
        try:
            analysis = asyncio.run(monitor.get_ai_analysis(glucose_data, treatment_data, days))
            return jsonify({'analysis': analysis})
        except RuntimeError as e:
            # 处理在非主线程中运行asyncio.run可能出现的问题
            if "cannot run loop while another loop is running" in str(e):
                loop = asyncio.get_event_loop()
                analysis = loop.run_until_complete(monitor.get_ai_analysis(glucose_data, treatment_data, days))
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
        return jsonify({'response': response})
    except RuntimeError as e:
        # 处理在非主线程中运行asyncio.run可能出现的问题
        if "cannot run loop while another loop is running" in str(e):
            loop = asyncio.get_event_loop()
            response = loop.run_until_complete(monitor.get_ai_consultation(question, include_data, days))
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

        glucose_data, treatment_data = loop.run_until_complete(
            monitor.fetch_nightscout_data(start_date, end_date)
        )

        if glucose_data:
            loop.run_until_complete(monitor.save_glucose_data(glucose_data))
        if treatment_data:
            loop.run_until_complete(monitor.save_treatment_data(treatment_data))

        loop.close()

        return jsonify({
            'success': True,
            'glucose_count': len(glucose_data),
            'treatment_count': len(treatment_data)
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
            if monitor.save_config(new_config):
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
        glucose_data, treatment_data = loop.run_until_complete(
            monitor.fetch_nightscout_data(today, today)
        )

        loop.close()

        if glucose_data or treatment_data:
            return jsonify({
                'success': True,
                'message': 'Nightscout连接正常',
                'glucose_count': len(glucose_data),
                'treatment_count': len(treatment_data)
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
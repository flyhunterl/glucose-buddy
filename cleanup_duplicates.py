#!/usr/bin/env python3
"""
数据库重复数据清理脚本
用于清理 treatment_data 表中的重复记录
"""

import sqlite3
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def cleanup_duplicate_treatments():
    """清理治疗数据表中的重复记录"""
    try:
        conn = sqlite3.connect("nightscout_data.db")
        cursor = conn.cursor()
        
        # 查询重复记录数量
        cursor.execute("""
            SELECT COUNT(*) as total_count,
                   COUNT(DISTINCT date_string, event_type, carbs, protein, fat, insulin, notes, duration) as unique_count
            FROM treatment_data
        """)
        
        total_count, unique_count = cursor.fetchone()
        duplicate_count = total_count - unique_count
        
        logger.info(f"治疗数据统计:")
        logger.info(f"  总记录数: {total_count}")
        logger.info(f"  唯一记录数: {unique_count}")
        logger.info(f"  重复记录数: {duplicate_count}")
        
        if duplicate_count > 0:
            # 备份原表
            logger.info("创建备份表...")
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS treatment_data_backup AS 
                SELECT * FROM treatment_data
            """)
            
            # 创建临时表保存唯一记录
            logger.info("创建临时表并保存唯一记录...")
            cursor.execute("""
                CREATE TEMPORARY TABLE treatment_data_temp AS
                SELECT 
                    MIN(id) as id,
                    date_string,
                    shanghai_time,
                    event_type,
                    carbs,
                    protein,
                    fat,
                    insulin,
                    notes,
                    duration,
                    MIN(created_at) as created_at
                FROM treatment_data
                GROUP BY date_string, event_type, carbs, protein, fat, insulin, notes, duration
            """)
            
            # 清空原表
            logger.info("清空原表...")
            cursor.execute("DELETE FROM treatment_data")
            
            # 插入唯一记录
            logger.info("插入唯一记录...")
            cursor.execute("""
                INSERT INTO treatment_data 
                SELECT * FROM treatment_data_temp
                ORDER BY created_at
            """)
            
            # 验证结果
            cursor.execute("SELECT COUNT(*) FROM treatment_data")
            final_count = cursor.fetchone()[0]
            
            logger.info(f"清理完成!")
            logger.info(f"  清理前: {total_count} 条记录")
            logger.info(f"  清理后: {final_count} 条记录")
            logger.info(f"  删除了: {total_count - final_count} 条重复记录")
            
            # 提交更改
            conn.commit()
            logger.info("数据库更改已提交")
            
        else:
            logger.info("没有发现重复记录，无需清理")
            
    except Exception as e:
        logger.error(f"清理过程中发生错误: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()

def cleanup_duplicate_glucose():
    """清理血糖数据表中的重复记录（如果有的话）"""
    try:
        conn = sqlite3.connect("nightscout_data.db")
        cursor = conn.cursor()
        
        # 查询重复记录数量
        cursor.execute("""
            SELECT COUNT(*) as total_count,
                   COUNT(DISTINCT date_string) as unique_count
            FROM glucose_data
        """)
        
        total_count, unique_count = cursor.fetchone()
        duplicate_count = total_count - unique_count
        
        logger.info(f"血糖数据统计:")
        logger.info(f"  总记录数: {total_count}")
        logger.info(f"  唯一记录数: {unique_count}")
        logger.info(f"  重复记录数: {duplicate_count}")
        
        if duplicate_count > 0:
            logger.info("发现血糖数据重复，开始清理...")
            
            # 保留最新的记录
            cursor.execute("""
                DELETE FROM glucose_data 
                WHERE id NOT IN (
                    SELECT MAX(id) 
                    FROM glucose_data 
                    GROUP BY date_string
                )
            """)
            
            deleted_count = cursor.rowcount
            conn.commit()
            
            logger.info(f"血糖数据清理完成，删除了 {deleted_count} 条重复记录")
        else:
            logger.info("血糖数据没有重复记录")
            
    except Exception as e:
        logger.error(f"清理血糖数据时发生错误: {e}")
        conn.rollback()
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    logger.info("开始清理数据库重复记录...")
    
    # 清理治疗数据重复
    cleanup_duplicate_treatments()
    
    # 清理血糖数据重复
    cleanup_duplicate_glucose()
    
    logger.info("数据库清理完成!")

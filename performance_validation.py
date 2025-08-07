#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
性能优化验证脚本
"""
import sys
import locale

# 设置控制台编码为UTF-8
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

import time
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from contextlib import contextmanager

class PerformanceValidator:
    def __init__(self):
        self.test_db_path = "test_performance.db"
        self._connection_pool = {}
        self._pool_lock = threading.Lock()
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
                        self.test_db_path, 
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
            print(f"数据库连接错误: {e}")
            raise
        finally:
            pass
            
    def setup_test_data(self):
        """设置测试数据"""
        conn = sqlite3.connect(self.test_db_path)
        cursor = conn.cursor()
        
        # 创建测试表
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
        
        # 创建索引
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_glucose_shanghai_time ON glucose_data(shanghai_time)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_glucose_sgv ON glucose_data(sgv)")
        
        # 生成测试数据
        test_data = []
        base_time = datetime.now() - timedelta(days=30)
        
        for i in range(1000):  # 生成1000条测试数据
            test_time = base_time + timedelta(minutes=i*5)
            sgv = 100 + (i % 40)  # 血糖值在100-140之间波动
            
            test_data.append({
                'date_string': test_time.strftime('%Y-%m-%d'),
                'shanghai_time': test_time.strftime('%Y-%m-%d %H:%M:%S'),
                'sgv': sgv,
                'direction': 'Flat',
                'trend': 0
            })
        
        # 插入测试数据
        for data in test_data:
            try:
                cursor.execute(
                    "INSERT OR REPLACE INTO glucose_data (date_string, shanghai_time, sgv, direction, trend) VALUES (?, ?, ?, ?, ?)",
                    (data['date_string'], data['shanghai_time'], data['sgv'], data['direction'], data['trend'])
                )
            except sqlite3.IntegrityError:
                pass
        
        conn.commit()
        conn.close()
        print(f"已生成 {len(test_data)} 条测试数据")
        
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
    
    def get_glucose_data_from_db(self, days: int = 7, use_cache: bool = True) -> List[Dict]:
        """从数据库获取血糖数据 - 性能优化版本"""
        # 生成缓存键
        cache_key = self._get_cache_key("glucose_data", days)
        
        # 尝试从缓存获取数据
        if use_cache:
            cached_data = self._get_cache(cache_key)
            if cached_data is not None:
                return cached_data
        
        try:
            with self.get_db_connection() as conn:
                cursor = conn.cursor()
                
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

            glucose_data = []
            for row in rows:
                glucose_data.append({
                    "dateString": row[0],
                    "shanghai_time": row[1],
                    "sgv": row[2],
                    "direction": row[3],
                    "trend": row[4]
                })
            
            # 缓存结果
            if use_cache and glucose_data:
                self._set_cache(cache_key, glucose_data, ttl_seconds=300)

            return glucose_data

        except Exception as e:
            print(f"从数据库获取血糖数据失败: {e}")
            return []
    
    def benchmark_performance(self):
        """性能基准测试"""
        print("开始性能基准测试...")
        
        # 测试无缓存性能
        print("测试无缓存性能...")
        start_time = time.time()
        for i in range(10):
            data = self.get_glucose_data_from_db(days=7, use_cache=False)
        no_cache_time = time.time() - start_time
        print(f"无缓存 10次查询耗时: {no_cache_time:.4f} 秒")
        
        # 测试有缓存性能
        print("测试有缓存性能...")
        start_time = time.time()
        for i in range(10):
            data = self.get_glucose_data_from_db(days=7, use_cache=True)
        cache_time = time.time() - start_time
        print(f"有缓存 10次查询耗时: {cache_time:.4f} 秒")
        
        # 计算性能提升
        if no_cache_time > 0:
            improvement = ((no_cache_time - cache_time) / no_cache_time) * 100
            print(f"性能提升: {improvement:.2f}%")
        
        # 测试并发性能
        print("测试并发性能...")
        def concurrent_test():
            for i in range(5):
                data = self.get_glucose_data_from_db(days=7, use_cache=True)
        
        threads = []
        start_time = time.time()
        for i in range(4):  # 4个线程并发
            thread = threading.Thread(target=concurrent_test)
            threads.append(thread)
            thread.start()
        
        for thread in threads:
            thread.join()
        
        concurrent_time = time.time() - start_time
        print(f"并发 4线程 20次查询耗时: {concurrent_time:.4f} 秒")
        
        return {
            'no_cache_time': no_cache_time,
            'cache_time': cache_time,
            'concurrent_time': concurrent_time,
            'improvement': improvement if no_cache_time > 0 else 0
        }
    
    def cleanup(self):
        """清理测试数据"""
        import os
        if os.path.exists(self.test_db_path):
            os.remove(self.test_db_path)
        
        # 清理连接池
        with self._pool_lock:
            for conn_key, conn in self._connection_pool.items():
                try:
                    conn.close()
                except:
                    pass
            self._connection_pool.clear()
        
        print("✅ 清理完成")

def main():
    """主函数"""
    print("性能优化验证开始...")
    
    validator = PerformanceValidator()
    
    try:
        # 设置测试数据
        validator.setup_test_data()
        
        # 运行性能基准测试
        results = validator.benchmark_performance()
        
        # 输出结果
        print("\n" + "="*50)
        print("性能优化验证结果:")
        print("="*50)
        print(f"无缓存查询耗时: {results['no_cache_time']:.4f} 秒")
        print(f"有缓存查询耗时: {results['cache_time']:.4f} 秒")
        print(f"并发查询耗时: {results['concurrent_time']:.4f} 秒")
        print(f"缓存性能提升: {results['improvement']:.2f}%")
        
        # 验证优化效果
        if results['improvement'] > 50:
            print("缓存优化效果显著")
        elif results['improvement'] > 20:
            print("缓存优化效果良好")
        else:
            print("缓存优化效果有限，可能需要调整缓存策略")
        
        print("性能优化验证完成")
        
    except Exception as e:
        print(f"验证过程中出现错误: {e}")
        
    finally:
        validator.cleanup()

if __name__ == "__main__":
    main()
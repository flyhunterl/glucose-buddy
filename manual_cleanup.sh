#!/bin/bash

# 手动清理重复数据脚本
# 用于在容器运行时清理数据库中的重复记录

echo "🧹 开始手动清理数据库重复记录..."

# 检查容器是否在运行
if ! docker-compose ps | grep -q "nightscout-web.*Up"; then
    echo "❌ 容器未运行，请先启动应用："
    echo "   docker-compose up -d"
    exit 1
fi

echo "📊 当前数据库状态："
docker-compose exec nightscout-web python -c "
import sqlite3
conn = sqlite3.connect('nightscout_data.db')
cursor = conn.cursor()

# 检查治疗数据
cursor.execute('SELECT COUNT(*) FROM treatment_data')
treatment_total = cursor.fetchone()[0]

cursor.execute('''
    SELECT COUNT(DISTINCT date_string, event_type, carbs, protein, fat, insulin, notes, duration) 
    FROM treatment_data
''')
treatment_unique = cursor.fetchone()[0]

# 检查血糖数据
cursor.execute('SELECT COUNT(*) FROM glucose_data')
glucose_total = cursor.fetchone()[0]

cursor.execute('SELECT COUNT(DISTINCT date_string) FROM glucose_data')
glucose_unique = cursor.fetchone()[0]

print(f'治疗数据: {treatment_total} 条记录, {treatment_unique} 条唯一记录, {treatment_total - treatment_unique} 条重复')
print(f'血糖数据: {glucose_total} 条记录, {glucose_unique} 条唯一记录, {glucose_total - glucose_unique} 条重复')

conn.close()
"

echo ""
echo "🔧 开始清理重复数据..."

# 在容器内运行清理脚本
docker-compose exec nightscout-web python cleanup_duplicates.py

echo ""
echo "✅ 清理完成！"
echo ""
echo "📊 清理后数据库状态："
docker-compose exec nightscout-web python -c "
import sqlite3
conn = sqlite3.connect('nightscout_data.db')
cursor = conn.cursor()

cursor.execute('SELECT COUNT(*) FROM treatment_data')
treatment_count = cursor.fetchone()[0]

cursor.execute('SELECT COUNT(*) FROM glucose_data')
glucose_count = cursor.fetchone()[0]

print(f'治疗数据: {treatment_count} 条记录')
print(f'血糖数据: {glucose_count} 条记录')

conn.close()
"

echo ""
echo "🎉 数据库清理完成！请刷新网页查看结果。"

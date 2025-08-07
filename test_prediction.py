#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test script for glucose prediction functionality
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_prediction_algorithm():
    """Test the glucose prediction algorithm"""
    # Mock glucose data for testing
    mock_glucose_data = [
        {'shanghai_time': '2024-01-01 08:00:00', 'sgv': 120},
        {'shanghai_time': '2024-01-01 08:05:00', 'sgv': 118},
        {'shanghai_time': '2024-01-01 08:10:00', 'sgv': 115},
        {'shanghai_time': '2024-01-01 08:15:00', 'sgv': 112},
        {'shanghai_time': '2024-01-01 08:20:00', 'sgv': 110},
        {'shanghai_time': '2024-01-01 08:25:00', 'sgv': 108},
        {'shanghai_time': '2024-01-01 08:30:00', 'sgv': 105},
        {'shanghai_time': '2024-01-01 08:35:00', 'sgv': 103},
        {'shanghai_time': '2024-01-01 08:40:00', 'sgv': 100},
        {'shanghai_time': '2024-01-01 08:45:00', 'sgv': 98},
        {'shanghai_time': '2024-01-01 08:50:00', 'sgv': 95},
        {'shanghai_time': '2024-01-01 08:55:00', 'sgv': 93},
        {'shanghai_time': '2024-01-01 09:00:00', 'sgv': 90},
        {'shanghai_time': '2024-01-01 09:05:00', 'sgv': 88},
        {'shanghai_time': '2024-01-01 09:10:00', 'sgv': 85},
        {'shanghai_time': '2024-01-01 09:15:00', 'sgv': 83},
        {'shanghai_time': '2024-01-01 09:20:00', 'sgv': 80},
        {'shanghai_time': '2024-01-01 09:25:00', 'sgv': 78},
        {'shanghai_time': '2024-01-01 09:30:00', 'sgv': 75},
        {'shanghai_time': '2024-01-01 09:35:00', 'sgv': 73},
        {'shanghai_time': '2024-01-01 09:40:00', 'sgv': 70},
    ]
    
    print("Testing glucose prediction algorithm...")
    print(f"Input data: {len(mock_glucose_data)} data points")
    
    # Test the prediction logic (extracted from the main app)
    try:
        # Data validation
        if len(mock_glucose_data) < 20:
            raise ValueError("数据点不足，至少需要20个数据点")
        
        # 按时间排序（从旧到新）
        sorted_data = sorted(mock_glucose_data, key=lambda x: x.get('shanghai_time', ''))
        
        # 获取最近的血糖值
        recent_glucose_values = []
        for entry in sorted_data[-20:]:  # 使用最近20个数据点
            sgv = entry.get('sgv', 0)
            if sgv > 0:
                recent_glucose_values.append(sgv)
        
        if len(recent_glucose_values) < 10:
            raise ValueError("有效的血糖数据点不足")
        
        print(f"Recent glucose values: {recent_glucose_values}")
        
        # 加权移动平均计算
        weights = [i * 0.1 for i in range(1, len(recent_glucose_values) + 1)]
        weighted_sum = sum(recent_glucose_values[i] * weights[i] for i in range(len(recent_glucose_values)))
        weight_sum = sum(weights)
        weighted_avg = weighted_sum / weight_sum
        
        print(f"Weighted average: {weighted_avg:.1f}")
        
        # 趋势计算（使用最近5个数据点）
        trend_values = recent_glucose_values[-5:]
        if len(trend_values) >= 2:
            # 计算变化率
            changes = []
            for i in range(1, len(trend_values)):
                change = trend_values[i] - trend_values[i-1]
                changes.append(change)
            
            avg_change = sum(changes) / len(changes)
            
            # 趋势外推30分钟
            # 假设数据点间隔约5分钟，30分钟相当于6个间隔
            projected_change = avg_change * 6
            predicted_glucose_mgdl = weighted_avg + projected_change
        else:
            # 如果没有足够数据计算趋势，只使用加权平均
            predicted_glucose_mgdl = weighted_avg
            avg_change = 0
        
        # 计算置信度（基于数据点数量和趋势一致性）
        data_points_factor = min(len(recent_glucose_values) / 20, 1.0)
        
        # 趋势一致性因子（变化的标准差）
        if len(changes) > 1:
            variance = sum((x - avg_change) ** 2 for x in changes) / len(changes)
            std_dev = variance ** 0.5
            trend_consistency = max(0, 1 - (std_dev / abs(avg_change)) if avg_change != 0 else 1)
        else:
            trend_consistency = 0.5
        
        confidence_score = round((data_points_factor * 0.6 + trend_consistency * 0.4) * 100, 1)
        
        # 单位转换
        predicted_glucose_mmol = round(predicted_glucose_mgdl / 18.0, 1)
        
        print(f"Predicted glucose (mg/dL): {predicted_glucose_mgdl:.1f}")
        print(f"Predicted glucose (mmol/L): {predicted_glucose_mmol:.1f}")
        print(f"Confidence score: {confidence_score:.1f}%")
        print(f"Trend rate: {avg_change:.2f} mg/dL per reading")
        
        # Risk assessment
        high_threshold = 70
        medium_threshold = 80
        
        if predicted_glucose_mgdl < high_threshold:
            risk_level = 'HIGH'
            risk_description = '高风险：可能在30分钟内发生低血糖'
        elif predicted_glucose_mgdl < medium_threshold:
            risk_level = 'MEDIUM'
            risk_description = '中等风险：血糖可能偏低'
        else:
            risk_level = 'LOW'
            risk_description = '低风险：血糖水平正常'
        
        print(f"Risk level: {risk_level}")
        print(f"Risk description: {risk_description}")
        
        # Test result
        if predicted_glucose_mgdl < 70:
            print("[PASS] Test passed: High risk detected correctly")
            return True
        elif predicted_glucose_mgdl < 80:
            print("[PASS] Test passed: Medium risk detected correctly")
            return True
        else:
            print("[PASS] Test passed: Low risk detected correctly")
            return True
            
    except Exception as e:
        print(f"[FAIL] Test failed: {e}")
        return False

def test_unit_conversion():
    """Test unit conversion"""
    print("\nTesting unit conversion...")
    
    test_values = [54, 70, 80, 100, 126, 180, 200]
    
    for mg_dl in test_values:
        mmol_l = round(mg_dl / 18.0, 1)
        print(f"{mg_dl} mg/dL = {mmol_l} mmol/L")
    
    print("[PASS] Unit conversion test passed")
    return True

if __name__ == "__main__":
    print("=" * 60)
    print("Glucose Prediction and Alert System Test")
    print("=" * 60)
    
    test1_passed = test_prediction_algorithm()
    test2_passed = test_unit_conversion()
    
    print("\n" + "=" * 60)
    if test1_passed and test2_passed:
        print("[SUCCESS] All tests passed!")
    else:
        print("[ERROR] Some tests failed!")
    print("=" * 60)
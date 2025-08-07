#!/usr/bin/env python3
"""
AI模型修复验证测试脚本
用于测试修复后的AI响应解析功能
"""

import asyncio
import aiohttp
import json
import logging
from datetime import datetime
from app import NightscoutWebMonitor

# 设置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class AIResponseTester:
    def __init__(self):
        self.monitor = NightscoutWebMonitor()
        
    def test_response_parsing(self):
        """测试响应解析功能"""
        logger.info("测试AI响应解析功能...")
        
        # 测试各种响应格式
        test_cases = [
            {
                "name": "OpenAI标准格式",
                "response": {
                    "choices": [
                        {
                            "message": {
                                "content": "这是OpenAI标准格式的响应"
                            }
                        }
                    ]
                },
                "expected": "这是OpenAI标准格式的响应"
            },
            {
                "name": "直接content格式",
                "response": {
                    "content": "这是直接content格式的响应"
                },
                "expected": "这是直接content格式的响应"
            },
            {
                "name": "Gemini格式",
                "response": {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "text": "这是Gemini格式的响应"
                                    }
                                ]
                            }
                        }
                    ]
                },
                "expected": "这是Gemini格式的响应"
            },
            {
                "name": "GLM数据包装格式",
                "response": {
                    "data": {
                        "choices": [
                            {
                                "content": "这是GLM数据包装格式的响应"
                            }
                        ]
                    }
                },
                "expected": "这是GLM数据包装格式的响应"
            },
            {
                "name": "直接text格式",
                "response": {
                    "text": "这是直接text格式的响应"
                },
                "expected": "这是直接text格式的响应"
            },
            {
                "name": "response格式",
                "response": {
                    "response": "这是response格式的响应"
                },
                "expected": "这是response格式的响应"
            },
            {
                "name": "answer格式",
                "response": {
                    "answer": "这是answer格式的响应"
                },
                "expected": "这是answer格式的响应"
            },
            {
                "name": "choices.text格式",
                "response": {
                    "choices": [
                        {
                            "text": "这是choices.text格式的响应"
                        }
                    ]
                },
                "expected": "这是choices.text格式的响应"
            },
            {
                "name": "choices.content格式",
                "response": {
                    "choices": [
                        {
                            "content": "这是choices.content格式的响应"
                        }
                    ]
                },
                "expected": "这是choices.content格式的响应"
            },
            {
                "name": "无效格式",
                "response": {
                    "invalid": "这是无效格式的响应"
                },
                "expected": None
            },
            {
                "name": "空响应",
                "response": {},
                "expected": None
            }
        ]
        
        success_count = 0
        total_count = len(test_cases)
        
        for i, test_case in enumerate(test_cases, 1):
            logger.info(f"测试用例 {i}: {test_case['name']}")
            
            try:
                result = self.monitor.parse_ai_response(test_case['response'])
                
                if result == test_case['expected']:
                    logger.info(f"✅ {test_case['name']}: 解析成功")
                    success_count += 1
                else:
                    logger.error(f"❌ {test_case['name']}: 解析失败")
                    logger.error(f"   期望: {test_case['expected']}")
                    logger.error(f"   实际: {result}")
                    
            except Exception as e:
                logger.error(f"❌ {test_case['name']}: 解析异常 - {e}")
        
        logger.info(f"\n解析功能测试结果: {success_count}/{total_count} 通过")
        return success_count == total_count

    def test_config_validation(self):
        """测试配置验证功能"""
        logger.info("测试AI配置验证功能...")
        
        # 测试各种配置
        test_configs = [
            {
                "name": "完整配置",
                "config": {
                    "ai_config": {
                        "api_url": "https://api.openai.com/v1/chat/completions",
                        "model_name": "gpt-3.5-turbo",
                        "api_key": "test-key",
                        "timeout": 60
                    }
                },
                "should_be_valid": True
            },
            {
                "name": "缺少API URL",
                "config": {
                    "ai_config": {
                        "model_name": "gpt-3.5-turbo",
                        "api_key": "test-key"
                    }
                },
                "should_be_valid": False
            },
            {
                "name": "缺少模型名称",
                "config": {
                    "ai_config": {
                        "api_url": "https://api.openai.com/v1/chat/completions",
                        "api_key": "test-key"
                    }
                },
                "should_be_valid": False
            },
            {
                "name": "无效URL格式",
                "config": {
                    "ai_config": {
                        "api_url": "invalid-url",
                        "model_name": "gpt-3.5-turbo"
                    }
                },
                "should_be_valid": False
            },
            {
                "name": "GLM模型配置",
                "config": {
                    "ai_config": {
                        "api_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
                        "model_name": "glm-4",
                        "api_key": "test-key"
                    }
                },
                "should_be_valid": True
            },
            {
                "name": "Gemini模型配置",
                "config": {
                    "ai_config": {
                        "api_url": "https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent",
                        "model_name": "gemini-pro",
                        "api_key": "test-key"
                    }
                },
                "should_be_valid": True
            },
            {
                "name": "本地配置",
                "config": {
                    "ai_config": {
                        "api_url": "http://localhost:11434/v1/chat/completions",
                        "model_name": "llama3.1:8b"
                    }
                },
                "should_be_valid": True
            }
        ]
        
        success_count = 0
        total_count = len(test_configs)
        
        # 临时替换配置进行测试
        original_config = self.monitor.config
        
        for i, test_case in enumerate(test_configs, 1):
            logger.info(f"测试配置 {i}: {test_case['name']}")
            
            try:
                self.monitor.config = test_case['config']
                validation = self.monitor.validate_ai_config()
                
                if validation['valid'] == test_case['should_be_valid']:
                    logger.info(f"✅ {test_case['name']}: 验证通过")
                    if validation['warnings']:
                        logger.info(f"   警告: {validation['warnings']}")
                    success_count += 1
                else:
                    logger.error(f"❌ {test_case['name']}: 验证失败")
                    logger.error(f"   期望有效: {test_case['should_be_valid']}")
                    logger.error(f"   实际有效: {validation['valid']}")
                    if validation['errors']:
                        logger.error(f"   错误: {validation['errors']}")
                        
            except Exception as e:
                logger.error(f"❌ {test_case['name']}: 验证异常 - {e}")
        
        # 恢复原始配置
        self.monitor.config = original_config
        
        logger.info(f"\n配置验证测试结果: {success_count}/{total_count} 通过")
        return success_count == total_count

    async def test_ai_connection_simulation(self):
        """模拟测试AI连接功能"""
        logger.info("测试AI连接功能（模拟）...")
        
        # 测试配置验证
        validation = self.monitor.validate_ai_config()
        
        if not validation['valid']:
            logger.error("配置验证失败，跳过连接测试")
            return False
        
        logger.info("配置验证通过")
        if validation['warnings']:
            logger.info(f"配置警告: {validation['warnings']}")
        
        # 注意：这里不进行实际的连接测试，因为可能需要真实的API密钥
        # 用户可以使用 /api/test-ai 端点进行实际测试
        logger.info("连接测试功能已就绪，使用 /api/test-ai 端点进行实际测试")
        
        return True

def main():
    """主测试函数"""
    logger.info("开始测试AI响应修复效果...")
    
    tester = AIResponseTester()
    
    # 测试响应解析功能
    parsing_success = tester.test_response_parsing()
    
    # 测试配置验证功能
    config_success = tester.test_config_validation()
    
    # 测试连接功能（模拟）
    connection_success = asyncio.run(tester.test_ai_connection_simulation())
    
    # 输出总体结果
    logger.info(f"\n{'='*60}")
    logger.info("测试结果汇总")
    logger.info(f"{'='*60}")
    logger.info(f"响应解析功能: {'✅ 通过' if parsing_success else '❌ 失败'}")
    logger.info(f"配置验证功能: {'✅ 通过' if config_success else '❌ 失败'}")
    logger.info(f"连接测试功能: {'✅ 通过' if connection_success else '❌ 失败'}")
    
    overall_success = parsing_success and config_success and connection_success
    logger.info(f"\n总体测试结果: {'✅ 所有测试通过' if overall_success else '❌ 部分测试失败'}")
    
    if overall_success:
        logger.info("\n🎉 修复验证成功！")
        logger.info("GLM4.5和Gemini模型现在应该能够正常返回响应")
        logger.info("建议步骤:")
        logger.info("1. 重启应用以启用新的解析逻辑")
        logger.info("2. 使用 /api/test-ai 端点测试AI连接")
        logger.info("3. 在配置页面中验证AI设置")
        logger.info("4. 尝试使用AI分析和咨询功能")
    else:
        logger.info("\n⚠️  部分测试失败，请检查修复代码")
    
    return overall_success

if __name__ == "__main__":
    success = main()
    exit(0 if success else 1)
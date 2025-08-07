#!/usr/bin/env python3
"""
AI模型测试脚本
用于测试不同AI模型的响应情况，诊断GLM4.5和Gemini模型返回空的问题
"""

import asyncio
import aiohttp
import json
import logging
from datetime import datetime

# 设置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class AIModelTester:
    def __init__(self, config):
        self.config = config
        
    async def test_model(self, model_name, api_url, api_key=None):
        """测试指定模型的响应"""
        logger.info(f"开始测试模型: {model_name}")
        
        # 构造测试请求
        request_data = {
            "model": model_name,
            "messages": [
                {
                    "role": "user",
                    "content": "你好，请回复'测试成功'来确认你可以正常工作。"
                }
            ],
            "temperature": 0.7,
            "top_p": 0.9,
            "max_tokens": 100,
            "stream": False
        }
        
        headers = {
            "Content-Type": "application/json"
        }
        
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        
        try:
            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                start_time = datetime.now()
                async with session.post(api_url, json=request_data, headers=headers) as response:
                    end_time = datetime.now()
                    elapsed = (end_time - start_time).total_seconds()
                    
                    logger.info(f"模型 {model_name} 响应时间: {elapsed:.2f}秒")
                    logger.info(f"HTTP状态码: {response.status}")
                    
                    if response.status == 200:
                        result = await response.json()
                        logger.info(f"完整响应: {json.dumps(result, indent=2, ensure_ascii=False)}")
                        
                        # 检查响应格式
                        if 'choices' in result and len(result['choices']) > 0:
                            if 'message' in result['choices'][0] and 'content' in result['choices'][0]['message']:
                                content = result['choices'][0]['message']['content'].strip()
                                logger.info(f"✅ 模型 {model_name} 返回内容: {content}")
                                return {
                                    "model": model_name,
                                    "success": True,
                                    "content": content,
                                    "status_code": response.status,
                                    "response_time": elapsed,
                                    "full_response": result
                                }
                            else:
                                logger.error(f"❌ 模型 {model_name} 响应格式错误 - 缺少message或content字段")
                                return {
                                    "model": model_name,
                                    "success": False,
                                    "error": "响应格式错误 - 缺少message或content字段",
                                    "status_code": response.status,
                                    "response_time": elapsed,
                                    "full_response": result
                                }
                        else:
                            logger.error(f"❌ 模型 {model_name} 响应格式错误 - 缺少choices数组")
                            return {
                                "model": model_name,
                                "success": False,
                                "error": "响应格式错误 - 缺少choices数组",
                                "status_code": response.status,
                                "response_time": elapsed,
                                "full_response": result
                            }
                    else:
                        error_text = await response.text()
                        logger.error(f"❌ 模型 {model_name} HTTP错误: {response.status}")
                        logger.error(f"错误响应: {error_text}")
                        return {
                            "model": model_name,
                            "success": False,
                            "error": f"HTTP错误: {response.status}",
                            "error_text": error_text,
                            "status_code": response.status,
                            "response_time": elapsed
                        }
                        
        except asyncio.TimeoutError:
            logger.error(f"❌ 模型 {model_name} 请求超时")
            return {
                "model": model_name,
                "success": False,
                "error": "请求超时",
                "status_code": None,
                "response_time": None
            }
        except Exception as e:
            logger.error(f"❌ 模型 {model_name} 请求异常: {e}")
            return {
                "model": model_name,
                "success": False,
                "error": str(e),
                "status_code": None,
                "response_time": None
            }

async def main():
    """主函数 - 测试所有模型"""
    
    # 配置不同的模型
    models_to_test = [
        {
            "name": "qwen-turbo",
            "description": "通义千问Turbo"
        },
        {
            "name": "deepseek-chat",
            "description": "DeepSeek Chat"
        },
        {
            "name": "glm-4",
            "description": "GLM4"
        },
        {
            "name": "glm-4-air",
            "description": "GLM4 Air"
        },
        {
            "name": "gemini-pro",
            "description": "Gemini Pro"
        }
    ]
    
    # API配置 (需要根据实际情况修改)
    api_configs = [
        {
            "name": "OpenAI兼容接口",
            "api_url": "http://localhost:11434/v1/chat/completions",  # Ollama
            "api_key": None
        },
        {
            "name": "阿里云DashScope",
            "api_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
            "api_key": "your-dashscope-api-key"
        },
        {
            "name": "DeepSeek官方",
            "api_url": "https://api.deepseek.com/v1/chat/completions",
            "api_key": "your-deepseek-api-key"
        },
        {
            "name": "智谱AI",
            "api_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
            "api_key": "your-zhipu-api-key"
        },
        {
            "name": "Google Gemini",
            "api_url": "https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent",
            "api_key": "your-gemini-api-key"
        }
    ]
    
    # 测试结果汇总
    all_results = []
    
    for api_config in api_configs:
        logger.info(f"\n{'='*60}")
        logger.info(f"测试API: {api_config['name']}")
        logger.info(f"API URL: {api_config['api_url']}")
        logger.info(f"{'='*60}")
        
        tester = AIModelTester(api_config)
        
        for model in models_to_test:
            logger.info(f"\n--- 测试模型: {model['name']} ({model['description']}) ---")
            
            result = await tester.test_model(
                model_name=model["name"],
                api_url=api_config["api_url"],
                api_key=api_config["api_key"]
            )
            
            all_results.append({
                "api_provider": api_config["name"],
                **result
            })
            
            # 添加延迟避免请求过频
            await asyncio.sleep(2)
    
    # 生成测试报告
    logger.info(f"\n{'='*80}")
    logger.info("测试结果汇总")
    logger.info(f"{'='*80}")
    
    successful_tests = [r for r in all_results if r["success"]]
    failed_tests = [r for r in all_results if not r["success"]]
    
    logger.info(f"✅ 成功测试: {len(successful_tests)}")
    logger.info(f"❌ 失败测试: {len(failed_tests)}")
    
    if successful_tests:
        logger.info("\n成功测试的模型:")
        for result in successful_tests:
            logger.info(f"  - {result['api_provider']} / {result['model']}: {result['content']}")
    
    if failed_tests:
        logger.info("\n失败测试的模型:")
        for result in failed_tests:
            logger.info(f"  - {result['api_provider']} / {result['model']}: {result['error']}")
    
    # 保存详细结果到文件
    with open("ai_model_test_results.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)
    
    logger.info(f"\n详细测试结果已保存到: ai_model_test_results.json")

if __name__ == "__main__":
    print("AI模型测试脚本")
    print("注意: 请先修改脚本中的API密钥配置")
    print("按Enter键开始测试，或Ctrl+C退出...")
    try:
        input()
    except KeyboardInterrupt:
        print("\n测试已取消")
        exit(0)
    
    asyncio.run(main())
#!/usr/bin/env python3
"""
Flash Loan Arbitrage Bot - النظام الرئيسي
"""

import asyncio
import json
import logging
import signal
import sys
from datetime import datetime
from typing import Dict, List, Optional

import yaml
from web3 import Web3
from eth_account import Account

from arbitrage_scanner import ArbitrageScanner
from mev_protector import MEVProtector
from transaction_executor import TransactionExecutor
from monitoring.metrics import MetricsCollector
from monitoring.alerts import AlertSystem
from monitoring.dashboard import DashboardServer

# إعداد التسجيل
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/bot.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

class FlashLoanArbitrageBot:
    """
    البوت الرئيسي لتنفيذ استراتيجيات المراجحة باستخدام Flash Loans
    """
    
    def __init__(self, config_path: str = "config.yaml"):
        # تحميل الإعدادات
        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)
        
        self._validate_config()
        
        # إعداد Web3
        self.w3_main = Web3(Web3.HTTPProvider(self.config['rpc']['mainnet']))
        self.w3_private = Web3(Web3.HTTPProvider(self.config['rpc']['private']))
        
        # إعداد Polygon
        self.w3_main.middleware_onion.inject(geth_poa_middleware, layer=0)
        self.w3_private.middleware_onion.inject(geth_poa_middleware, layer=0)
        
        # الحسابات
        self.owner = Account.from_key(self.config['keys']['owner'])
        self.executor = Account.from_key(self.config['keys']['executor'])
        
        # تحميل العقد
        with open(self.config['contracts']['abi_path'], 'r') as f:
            contract_abi = json.load(f)
        
        self.contract = self.w3_main.eth.contract(
            address=self.config['contracts']['flashloan_arbitrage'],
            abi=contract_abi
        )
        
        # المكونات
        self.scanner = ArbitrageScanner(self)
        self.mev_protector = MEVProtector(self)
        self.executor_module = TransactionExecutor(self)
        self.metrics = MetricsCollector(self)
        self.alerts = AlertSystem(self)
        self.dashboard = DashboardServer(self)
        
        # الحالة
        self.is_running = False
        self.last_scan_time = 0
        self.active_trades = {}
        self.trade_history = []
        
        # الإحصائيات
        self.stats = {
            'total_scans': 0,
            'opportunities_found': 0,
            'trades_executed': 0,
            'trades_successful': 0,
            'total_profit': 0,
            'total_gas_cost': 0,
            'uptime_start': datetime.now()
        }
        
        logger.info("🚀 Flash Loan Arbitrage Bot initialized")
        logger.info(f"📋 Contract: {self.contract.address}")
        logger.info(f"👤 Owner: {self.owner.address}")
        logger.info(f"⚡ Executor: {self.executor.address}")
    
    def _validate_config(self):
        """التحقق من صحة الإعدادات"""
        required_fields = [
            'rpc.mainnet',
            'rpc.private',
            'keys.owner',
            'keys.executor',
            'contracts.flashloan_arbitrage',
            'contracts.abi_path',
            'trading.min_profit',
            'trading.max_slippage',
            'trading.check_interval'
        ]
        
        for field in required_fields:
            keys = field.split('.')
            value = self.config
            for key in keys:
                if key not in value:
                    raise ValueError(f"Missing config field: {field}")
                value = value[key]
    
    async def start(self):
        """بدء تشغيل البوت"""
        self.is_running = True
        
        # إعداد معالجات الإشارات
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)
        
        logger.info("🚀 Starting Flash Loan Arbitrage Bot...")
        
        # بدء المكونات
        await self.dashboard.start()
        self.metrics.start_collecting()
        
        # المهام الرئيسية
        tasks = [
            self._run_scanning_loop(),
            self._run_execution_loop(),
            self._run_monitoring_loop(),
            self._run_health_check_loop()
        ]
        
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            logger.info("Bot tasks cancelled")
        except Exception as e:
            logger.error(f"Bot error: {e}", exc_info=True)
        finally:
            await self.stop()
    
    async def _run_scanning_loop(self):
        """حلقة فحص فرص المراجحة"""
        while self.is_running:
            try:
                start_time = datetime.now()
                
                # فحص الفرص
                opportunities = await self.scanner.scan_opportunities()
                self.stats['total_scans'] += 1
                
                if opportunities:
                    self.stats['opportunities_found'] += len(opportunities)
                    logger.info(f"🔍 Found {len(opportunities)} opportunities")
                    
                    # إضافة الفرص إلى قائمة الانتظار
                    for opp in opportunities:
                        await self._process_opportunity(opp)
                
                # حساب وقت المسح
                scan_time = (datetime.now() - start_time).total_seconds()
                self.last_scan_time = scan_time
                
                # الانتظار للدورة التالية
                await asyncio.sleep(self.config['trading']['check_interval'])
                
            except Exception as e:
                logger.error(f"Scanning error: {e}", exc_info=True)
                await asyncio.sleep(5)
    
    async def _process_opportunity(self, opportunity: Dict):
        """معالجة فرصة مراجحة"""
        try:
            # التحقق من الربحية بعد احتساب الغاز
            gas_cost = await self.executor_module.estimate_gas_cost()
            net_profit = opportunity['expected_profit'] - gas_cost
            
            if net_profit < self.config['trading']['min_profit']:
                return
            
            # إضافة حماية MEV
            protected_opportunity = await self.mev_protector.protect_opportunity(opportunity)
            
            # إضافة إلى قائمة الانتظار
            trade_id = f"{opportunity['base_asset']}_{int(datetime.now().timestamp())}"
            self.active_trades[trade_id] = {
                'id': trade_id,
                'opportunity': protected_opportunity,
                'status': 'pending',
                'created_at': datetime.now(),
                'estimated_profit': net_profit
            }
            
            logger.info(f"✅ Queued trade {trade_id} - Estimated profit: {net_profit/1e18:.4f} MATIC")
            
        except Exception as e:
            logger.error(f"Error processing opportunity: {e}")
    
    async def _run_execution_loop(self):
        """حلقة تنفيذ الصفقات"""
        while self.is_running:
            try:
                # البحث عن أفضل صفقة قيد الانتظار
                pending_trades = [
                    t for t in self.active_trades.values() 
                    if t['status'] == 'pending'
                ]
                
                if pending_trades:
                    # اختيار أفضل صفقة (أعلى ربح)
                    best_trade = max(pending_trades, key=lambda x: x['estimated_profit'])
                    
                    # تغيير الحالة إلى جاري التنفيذ
                    best_trade['status'] = 'executing'
                    best_trade['execution_start'] = datetime.now()
                    
                    # تنفيذ الصفقة
                    success = await self.executor_module.execute_trade(best_trade)
                    
                    # تحديث الحالة
                    if success:
                        best_trade['status'] = 'success'
                        self.stats['trades_successful'] += 1
                    else:
                        best_trade['status'] = 'failed'
                    
                    best_trade['execution_end'] = datetime.now()
                    best_trade['execution_time'] = (
                        best_trade['execution_end'] - best_trade['execution_start']
                    ).total_seconds()
                    
                    # نقل إلى التاريخ
                    self.trade_history.append(best_trade)
                    del self.active_trades[best_trade['id']]
                    
                    self.stats['trades_executed'] += 1
                    
                    # إرسال إنذار
                    await self.alerts.send_trade_alert(best_trade)
                
                await asyncio.sleep(0.1)  # 100ms
                
            except Exception as e:
                logger.error(f"Execution error: {e}", exc_info=True)
                await asyncio.sleep(1)
    
    async def _run_monitoring_loop(self):
        """حلقة المراقبة"""
        while self.is_running:
            try:
                # تحديث المقاييس
                self.metrics.update_metrics(self.stats, self.active_trades)
                
                # التحقق من الإنذارات
                await self._check_alerts()
                
                # تسجيل الإحصائيات
                if self.stats['trades_executed'] % 10 == 0:
                    self._log_statistics()
                
                await asyncio.sleep(30)  # كل 30 ثانية
                
            except Exception as e:
                logger.error(f"Monitoring error: {e}")
                await asyncio.sleep(10)
    
    async def _run_health_check_loop(self):
        """حلقة فحص صحة النظام"""
        while self.is_running:
            try:
                # فحص اتصال RPC
                mainnet_ok = self.w3_main.isConnected()
                private_ok = self.w3_private.isConnected()
                
                if not mainnet_ok:
                    logger.error("⚠️ Mainnet RPC connection lost")
                    await self.alerts.send_system_alert("Mainnet RPC disconnected")
                
                if not private_ok:
                    logger.error("⚠️ Private RPC connection lost")
                    await self.alerts.send_system_alert("Private RPC disconnected")
                
                # فحص رصيد الغاز
                balance = self.w3_main.eth.get_balance(self.executor.address)
                if balance < self.config['trading']['min_executor_balance']:
                    logger.warning(f"⚠️ Low executor balance: {balance/1e18:.4f} MATIC")
                    await self.alerts.send_system_alert(f"Low executor balance: {balance/1e18:.2f} MATIC")
                
                # فحص العقد
                try:
                    is_paused = self.contract.functions.paused().call()
                    if is_paused:
                        logger.warning("⚠️ Contract is paused")
                except Exception as e:
                    logger.error(f"Contract check failed: {e}")
                
                await asyncio.sleep(60)  # كل دقيقة
                
            except Exception as e:
                logger.error(f"Health check error: {e}")
                await asyncio.sleep(30)
    
    async def _check_alerts(self):
        """فحص الإنذارات"""
        # إنذارات الربح المرتفع
        recent_trades = [
            t for t in self.trade_history[-10:] 
            if t['status'] == 'success'
        ]
        
        if recent_trades:
            total_profit = sum(t.get('actual_profit', 0) for t in recent_trades)
            if total_profit > self.config['alerts']['high_profit_threshold']:
                await self.alerts.send_profit_alert(total_profit, len(recent_trades))
        
        # إنذارات الفشل المتتالي
        failed_trades = [
            t for t in self.trade_history[-5:] 
            if t['status'] == 'failed'
        ]
        
        if len(failed_trades) >= 3:
            await self.alerts.send_failure_alert(len(failed_trades))
    
    def _log_statistics(self):
        """تسجيل الإحصائيات"""
        uptime = datetime.now() - self.stats['uptime_start']
        
        logger.info("\n" + "="*60)
        logger.info("📊 BOT STATISTICS")
        logger.info("="*60)
        logger.info(f"Uptime: {uptime}")
        logger.info(f"Total scans: {self.stats['total_scans']}")
        logger.info(f"Opportunities found: {self.stats['opportunities_found']}")
        logger.info(f"Trades executed: {self.stats['trades_executed']}")
        logger.info(f"Successful trades: {self.stats['trades_successful']}")
        
        if self.stats['trades_executed'] > 0:
            success_rate = (self.stats['trades_successful'] / self.stats['trades_executed']) * 100
            logger.info(f"Success rate: {success_rate:.2f}%")
        
        logger.info(f"Total profit: {self.stats['total_profit']/1e18:.4f} MATIC")
        logger.info(f"Total gas cost: {self.stats['total_gas_cost']/1e18:.4f} MATIC")
        logger.info(f"Net profit: {(self.stats['total_profit'] - self.stats['total_gas_cost'])/1e18:.4f} MATIC")
        logger.info(f"Active trades: {len(self.active_trades)}")
        logger.info(f"Last scan time: {self.last_scan_time:.3f}s")
        logger.info("="*60)
    
    def _handle_shutdown(self, signum, frame):
        """معالجة إغلاق البوت"""
        logger.info(f"Received signal {signum}, shutting down...")
        self.is_running = False
    
    async def stop(self):
        """إيقاف البوت"""
        self.is_running = False
        
        # إيقاف المكونات
        await self.dashboard.stop()
        self.metrics.stop_collecting()
        
        # حفظ البيانات
        self._save_data()
        
        logger.info("🛑 Flash Loan Arbitrage Bot stopped")
    
    def _save_data(self):
        """حفظ بيانات البوت"""
        try:
            data = {
                'stats': self.stats,
                'trade_history': [
                    {
                        'id': t['id'],
                        'status': t['status'],
                        'created_at': t['created_at'].isoformat() if 'created_at' in t else None,
                        'estimated_profit': t.get('estimated_profit', 0),
                        'actual_profit': t.get('actual_profit', 0)
                    }
                    for t in self.trade_history[-100:]  # حفظ آخر 100 صفقة
                ],
                'active_trades': list(self.active_trades.keys()),
                'shutdown_time': datetime.now().isoformat()
            }
            
            with open('logs/bot_state.json', 'w') as f:
                json.dump(data, f, indent=2, default=str)
            
            logger.info("💾 Bot state saved")
        except Exception as e:
            logger.error(f"Error saving bot state: {e}")

async def main():
    """الدالة الرئيسية"""
    # إنشاء البوت
    bot = FlashLoanArbitrageBot("config.yaml")
    
    try:
        # بدء البوت
        await bot.start()
    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # التأكد من إيقاف البوت
        if hasattr(bot, 'is_running') and bot.is_running:
            await bot.stop()

if __name__ == "__main__":
    # تشغيل البوت
    asyncio.run(main())

"""
منفذ المعاملات
"""

import time
from typing import Dict, Optional
from web3 import Web3
from eth_account import Account

class TransactionExecutor:
    """
    تنفيذ المعاملات على الشبكة
    """
    
    def __init__(self, bot):
        self.bot = bot
        self.w3_main = bot.w3_main
        self.w3_private = bot.w3_private
        
        # الإعدادات
        self.config = bot.config.get('execution', {})
        
        # الحالة
        self.execution_queue = []
        self.active_executions = {}
        
        # الإحصائيات
        self.stats = {
            'total_executions': 0,
            'successful_executions': 0,
            'failed_executions': 0,
            'total_gas_used': 0,
            'total_gas_cost': 0,
            'avg_execution_time': 0
        }
    
    async def execute_trade(self, trade: Dict) -> bool:
        """تنفيذ صفقة Flash Loan"""
        start_time = time.time()
        
        try:
            opportunity = trade['opportunity']
            
            # 1. بناء المعاملة
            tx_data = await self._build_transaction(opportunity)
            
            # 2. توقيع المعاملة
            signed_tx = self.bot.executor.sign_transaction(tx_data)
            
            # 3. إرسال المعاملة (خاصة أو عادية)
            if self.config.get('use_private_tx', True):
                tx_hash = await self._send_private_transaction(signed_tx.rawTransaction)
            else:
                tx_hash = await self._send_regular_transaction(signed_tx.rawTransaction)
            
            if not tx_hash:
                return False
            
            trade['tx_hash'] = tx_hash
            self.active_executions[tx_hash] = trade
            
            # 4. انتظار التنفيذ
            success = await self._wait_for_execution(tx_hash)
            
            # 5. تسجيل النتيجة
            execution_time = time.time() - start_time
            
            if success:
                self.stats['successful_executions'] += 1
                trade['status'] = 'success'
                trade['execution_time'] = execution_time
                
                # حساب الربح الفعلي
                actual_profit = await self._calculate_actual_profit(tx_hash)
                trade['actual_profit'] = actual_profit
                self.bot.stats['total_profit'] += actual_profit
            else:
                self.stats['failed_executions'] += 1
                trade['status'] = 'failed'
            
            self.stats['total_executions'] += 1
            self.stats['avg_execution_time'] = (
                (self.stats['avg_execution_time'] * (self.stats['total_executions'] - 1) + execution_time) 
                / self.stats['total_executions']
            )
            
            # تحديث الرصيد المتوقع
            self._update_gas_stats(tx_hash)
            
            # تنظيف الذاكرة
            if tx_hash in self.active_executions:
                del self.active_executions[tx_hash]
            
            return success
            
        except Exception as e:
            logger.error(f"Trade execution failed: {e}", exc_info=True)
            return False
    
    async def _build_transaction(self, opportunity: Dict) -> Dict:
        """بناء معاملة Flash Loan"""
        # إعداد معلمات العقد
        params = (
            opportunity.get('strategy', 0),  # strategy
            opportunity['base_asset'],       # loanAsset
            opportunity['trade_size'],       # loanAmount
            opportunity['buy_router'],       # dexRouter1
            opportunity['sell_router'],      # dexRouter2
            opportunity['buy_path'],         # buyPath
            opportunity['sell_path'],        # sellPath
            opportunity.get('min_out_buy', opportunity['trade_size']),  # minOutBuy
            opportunity.get('min_out_sell', opportunity['trade_size']), # minOutSell
            self.bot.config['trading']['min_profit'],  # minProfit
            opportunity['base_asset'],       # profitToken
            opportunity['nonce'],            # nonce
            opportunity.get('deadline', int(time.time() + 300)),  # deadline
            opportunity.get('max_gas_price', self.w3_main.eth.gas_price * 2)  # maxGasPrice
        )
        
        # بناء المعاملة
        tx = self.bot.contract.functions.executeFlashLoan(
            params,
            opportunity['signature']
        ).build_transaction({
            'from': self.bot.executor.address,
            'nonce': self.w3_main.eth.get_transaction_count(self.bot.executor.address),
            'gas': 1000000,  # تقدير آمن
            'maxFeePerGas': opportunity.get('max_gas_price', self.w3_main.eth.gas_price * 2),
            'maxPriorityFeePerGas': self.w3_main.eth.gas_price,
            'chainId': 137  # Polygon
        })
        
        return tx
    
    async def _send_private_transaction(self, raw_tx: bytes) -> Optional[str]:
        """إرسال معاملة خاصة"""
        try:
            tx_hash = self.w3_private.eth.send_raw_transaction(raw_tx)
            logger.info(f"📤 Private transaction sent: {tx_hash.hex()}")
            return tx_hash.hex()
        except Exception as e:
            logger.error(f"Failed to send private transaction: {e}")
            return None
    
    async def _send_regular_transaction(self, raw_tx: bytes) -> Optional[str]:
        """إرسال معاملة عادية"""
        try:
            tx_hash = self.w3_main.eth.send_raw_transaction(raw_tx)
            logger.info(f"📤 Regular transaction sent: {tx_hash.hex()}")
            return tx_hash.hex()
        except Exception as e:
            logger.error(f"Failed to send regular transaction: {e}")
            return None
    
    async def _wait_for_execution(self, tx_hash: str, timeout: int = 120) -> bool:
        """انتظار تأكيد المعاملة"""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            try:
                receipt = self.w3_main.eth.get_transaction_receipt(tx_hash)
                
                if receipt is not None:
                    if receipt.status == 1:
                        logger.info(f"✅ Transaction confirmed: {tx_hash}")
                        return True
                    else:
                        logger.error(f"❌ Transaction failed: {tx_hash}")
                        return False
            except Exception as e:
                logger.debug(f"Waiting for receipt: {e}")
            
            await asyncio.sleep(0.5)
        
        logger.warning(f"⏰ Transaction timeout: {tx_hash}")
        return False
    
    async def _calculate_actual_profit(self, tx_hash: str) -> int:
        """حساب الربح الفعلي من المعاملة"""
        try:
            # الحصول على أحداث المعاملة
            receipt = self.w3_main.eth.get_transaction_receipt(tx_hash)
            
            if receipt and receipt.logs:
                # تحليل الأحداث للعثور على ربح الصفقة
                # هذا يعتمد على events العقد
                pass
            
            # للتبسيط، نعود إلى الربح المتوقع
            return 0
            
        except Exception as e:
            logger.error(f"Error calculating actual profit: {e}")
            return 0
    
    def _update_gas_stats(self, tx_hash: str):
        """تحديث إحصائيات الغاز"""
        try:
            receipt = self.w3_main.eth.get_transaction_receipt(tx_hash)
            if receipt:
                gas_used = receipt.gasUsed
                gas_price = receipt.effectiveGasPrice
                gas_cost = gas_used * gas_price
                
                self.stats['total_gas_used'] += gas_used
                self.stats['total_gas_cost'] += gas_cost
                self.bot.stats['total_gas_cost'] += gas_cost
        except Exception as e:
            logger.error(f"Error updating gas stats: {e}")
    
    async def estimate_gas_cost(self) -> int:
        """تقدير تكلفة الغاز"""
        try:
            gas_price = self.w3_main.eth.gas_price
            
            # تقدير الغاز المطلوب لمعاملة Flash Loan
            estimated_gas = 500000  # تقدير معقول
            
            return gas_price * estimated_gas
            
        except Exception as e:
            logger.error(f"Error estimating gas cost: {e}")
            return 0
    
    def get_execution_stats(self) -> Dict:
        """الحصول على إحصائيات التنفيذ"""
        return self.stats

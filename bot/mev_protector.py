"""
نظام حماية MEV ومنع Front-running
"""

import hashlib
import time
from typing import Dict, Optional
from eth_account import Account, messages
from web3 import Web3

class MEVProtector:
    """
    حماية المعاملات من MEV وFront-running
    """
    
    def __init__(self, bot):
        self.bot = bot
        self.w3 = bot.w3_private
        
        # إعدادات MEV
        self.config = bot.config.get('mev_protection', {})
        
        # ذاكرة التخزين المؤقت للمعاملات
        self.pending_transactions = {}
        self.recent_blocks = []
        
        # إحصائيات
        self.stats = {
            'protected_txs': 0,
            'frontrunning_attempts': 0,
            'private_txs_sent': 0,
            'avg_protection_time': 0
        }
    
    async def protect_opportunity(self, opportunity: Dict) -> Dict:
        """إضافة حماية MEV لفرصة المراجحة"""
        protected_opportunity = opportunity.copy()
        
        # 1. إضافة Nonce فريد
        protected_opportunity['nonce'] = self._generate_unique_nonce(opportunity)
        
        # 2. إضافة timestamp لتجنب إعادة الاستخدام
        protected_opportunity['timestamp'] = int(time.time())
        
        # 3. إضافة حاجز سعر الغاز
        protected_opportunity['max_gas_price'] = self._calculate_max_gas_price()
        
        # 4. إنشاء توقيع EIP-712
        signature = await self._create_eip712_signature(protected_opportunity)
        protected_opportunity['signature'] = signature
        
        # 5. إضافة hash للمعاملة
        tx_hash = self._calculate_tx_hash(protected_opportunity)
        protected_opportunity['tx_hash'] = tx_hash
        
        # تسجيل المعاملة المعلقة
        self.pending_transactions[tx_hash] = {
            'opportunity': protected_opportunity,
            'created_at': time.time(),
            'status': 'protected'
        }
        
        self.stats['protected_txs'] += 1
        
        return protected_opportunity
    
    def _generate_unique_nonce(self, opportunity: Dict) -> int:
        """إنشاء nonce فريد بناءً على الفرصة"""
        # استخدام hash للفرصة + timestamp
        data = f"{opportunity['base_asset']}{opportunity['quote_asset']}{time.time_ns()}"
        hash_bytes = hashlib.sha256(data.encode()).digest()
        return int.from_bytes(hash_bytes[:8], 'big')
    
    def _calculate_max_gas_price(self) -> int:
        """حساب الحد الأقصى لسعر الغاز"""
        current_gas = self.w3.eth.gas_price
        
        # إضافة نسبة أمان (20%)
        max_gas = int(current_gas * 1.2)
        
        # الحد الأقصى المطلق
        absolute_max = self.config.get('max_gas_price_absolute', 500 * 10**9)  # 500 Gwei
        
        return min(max_gas, absolute_max)
    
    async def _create_eip712_signature(self, opportunity: Dict) -> str:
        """إنشاء توقيع EIP-712"""
        # إعداد Domain Separator
        domain = {
            'name': 'FlashLoanArbitrage',
            'version': '1.0.0',
            'chainId': 137,  # Polygon
            'verifyingContract': self.bot.contract.address
        }
        
        # أنواع الرسالة
        types = {
            'ExecuteFlashLoan': [
                {'name': 'strategy', 'type': 'uint8'},
                {'name': 'loanAsset', 'type': 'address'},
                {'name': 'loanAmount', 'type': 'uint256'},
                {'name': 'dexRouter1', 'type': 'address'},
                {'name': 'dexRouter2', 'type': 'address'},
                {'name': 'buyPathHash', 'type': 'bytes32'},
                {'name': 'sellPathHash', 'type': 'bytes32'},
                {'name': 'minOutBuy', 'type': 'uint256'},
                {'name': 'minOutSell', 'type': 'uint256'},
                {'name': 'minProfit', 'type': 'uint256'},
                {'name': 'profitToken', 'type': 'address'},
                {'name': 'nonce', 'type': 'uint256'},
                {'name': 'deadline', 'type': 'uint256'},
                {'name': 'maxGasPrice', 'type': 'uint256'}
            ]
        }
        
        # البيانات
        message = {
            'strategy': 0,  # ARBITRAGE
            'loanAsset': opportunity['base_asset'],
            'loanAmount': opportunity['trade_size'],
            'dexRouter1': opportunity['buy_router'],
            'dexRouter2': opportunity['sell_router'],
            'buyPathHash': Web3.keccak(abi.encode(opportunity['buy_path'])),
            'sellPathHash': Web3.keccak(abi.encode(opportunity['sell_path'])),
            'minOutBuy': opportunity.get('min_out_buy', opportunity['trade_size']),
            'minOutSell': opportunity.get('min_out_sell', opportunity['trade_size']),
            'minProfit': self.bot.config['trading']['min_profit'],
            'profitToken': opportunity['base_asset'],
            'nonce': opportunity['nonce'],
            'deadline': int(time.time() + 300),  # 5 دقائق
            'maxGasPrice': opportunity['max_gas_price']
        }
        
        # التوقيع
        signed_message = self.bot.owner.sign_typed_data(
            domain_data=domain,
            message_types=types,
            message_data=message
        )
        
        return signed_message.signature.hex()
    
    def _calculate_tx_hash(self, opportunity: Dict) -> str:
        """حساب hash فريد للمعاملة"""
        data = (
            f"{opportunity['base_asset']}"
            f"{opportunity['trade_size']}"
            f"{opportunity['nonce']}"
            f"{opportunity['timestamp']}"
            f"{self.bot.executor.address}"
        )
        return hashlib.sha256(data.encode()).hexdigest()
    
    async def monitor_for_frontrunning(self):
        """مراقبة محاولات Front-running"""
        try:
            # الحصول على أحدث المعاملات في الميمبول
            pending_txs = self.w3.eth.get_block('pending', full_transactions=True)
            
            for tx in pending_txs.transactions:
                # التحقق مما إذا كانت المعاملة تتنافس مع معاملاتنا
                if self._is_competing_transaction(tx):
                    self.stats['frontrunning_attempts'] += 1
                    
                    # تسجيل محاولة Front-running
                    logger.warning(f"⚠️ Front-running attempt detected: {tx.hash.hex()}")
                    
                    # يمكن إضافة منطق للتعامل مع Front-running هنا
                    # مثل زيادة سعر الغاز أو إلغاء المعاملة
        
        except Exception as e:
            logger.debug(f"Error monitoring frontrunning: {e}")
    
    def _is_competing_transaction(self, tx) -> bool:
        """التحقق مما إذا كانت المعاملة تتنافس مع معاملاتنا"""
        # التحقق من عنوان المتلقي
        if tx.to and tx.to.lower() == self.bot.contract.address.lower():
            return True
        
        # التحقق من بيانات المعاملة
        if tx.input and len(tx.input) > 10:
            # يمكن إضافة تحليل أكثر تعقيداً هنا
            pass
        
        return False
    
    async def send_private_transaction(self, raw_tx: str) -> Optional[str]:
        """إرسال معاملة خاصة عبر Private RPC"""
        try:
            tx_hash = self.w3.eth.send_raw_transaction(raw_tx)
            self.stats['private_txs_sent'] += 1
            return tx_hash.hex()
        except Exception as e:
            logger.error(f"Failed to send private transaction: {e}")
            return None
    
    def get_protection_stats(self) -> Dict:
        """الحصول على إحصائيات الحماية"""
        return self.stats

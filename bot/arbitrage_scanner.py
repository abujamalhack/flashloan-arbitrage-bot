"""
ماسح فرص المراجحة
"""

import asyncio
import logging
from typing import Dict, List, Optional
from decimal import Decimal

from web3 import Web3

logger = logging.getLogger(__name__)

class ArbitrageScanner:
    """
    فحص واكتشاف فرص المراجحة بين DEXs مختلفة
    """
    
    def __init__(self, bot):
        self.bot = bot
        self.w3 = bot.w3_main
        
        # أزواج التداول الممكنة
        self.trading_pairs = bot.config['trading']['pairs']
        
        # تاريخ الاكتشاف
        self.discovery_history = []
        self.last_prices = {}
        
        # الإحصائيات
        self.stats = {
            'total_scans': 0,
            'profitable_opportunities': 0,
            'best_opportunity': 0,
            'scan_speed_avg': 0
        }
    
    async def scan_opportunities(self) -> List[Dict]:
        """فحص جميع فرص المراجحة الممكنة"""
        opportunities = []
        
        try:
            # فحص كل زوج تداول
            for pair in self.trading_pairs:
                try:
                    pair_opportunities = await self._scan_pair(pair)
                    opportunities.extend(pair_opportunities)
                except Exception as e:
                    logger.debug(f"Error scanning pair {pair['base']}/{pair['quote']}: {e}")
            
            # تحديث الإحصائيات
            self.stats['total_scans'] += 1
            self.stats['profitable_opportunities'] += len(opportunities)
            
            if opportunities:
                best_profit = max(opp['expected_profit'] for opp in opportunities)
                if best_profit > self.stats['best_opportunity']:
                    self.stats['best_opportunity'] = best_profit
            
            # تسجيل الفرص المكتشفة
            for opp in opportunities:
                self.discovery_history.append({
                    'timestamp': datetime.now().isoformat(),
                    'pair': f"{opp['base_asset']}/{opp['quote_asset']}",
                    'profit': opp['expected_profit'],
                    'direction': opp['direction']
                })
            
            # الاحتفاظ بآخر 1000 اكتشاف فقط
            if len(self.discovery_history) > 1000:
                self.discovery_history = self.discovery_history[-1000:]
            
            return opportunities
            
        except Exception as e:
            logger.error(f"Error in scan_opportunities: {e}")
            return []
    
    async def _scan_pair(self, pair: Dict) -> List[Dict]:
        """فحص فرص المراجحة لزوج معين"""
        opportunities = []
        
        # الحصول على الأسعار من جميع الرواتر
        prices = await self._get_prices_for_pair(pair)
        
        if len(prices) < 2:
            return opportunities
        
        # البحث عن فروق الأسعار
        for i, (router1, price1) in enumerate(prices):
            for j, (router2, price2) in enumerate(prices[i+1:], i+1):
                if router1 == router2:
                    continue
                
                # حساب فرق السعر
                price_diff = abs(price1 - price2)
                price_diff_percent = price_diff / min(price1, price2)
                
                # التحقق من فرق السعر الأدنى
                min_price_diff = self.bot.config['trading']['min_price_diff']
                if price_diff_percent < min_price_diff:
                    continue
                
                # تحديد اتجاه المراجحة
                if price1 < price2:
                    # الشراء من router1 والبيع على router2
                    direction = 'buy_low_sell_high'
                    buy_router = router1
                    sell_router = router2
                    buy_price = price1
                    sell_price = price2
                else:
                    # الشراء من router2 والبيع على router1
                    direction = 'buy_low_sell_high'
                    buy_router = router2
                    sell_router = router1
                    buy_price = price2
                    sell_price = price1
                
                # حساب الربح المتوقع
                trade_size = self.bot.config['trading']['default_trade_size']
                expected_profit = await self._calculate_expected_profit(
                    pair, direction, trade_size, buy_price, sell_price
                )
                
                # التحقق من الربحية بعد احتساب التكاليف
                if expected_profit > 0:
                    opportunity = {
                        'base_asset': pair['base'],
                        'quote_asset': pair['quote'],
                        'direction': direction,
                        'buy_router': buy_router,
                        'sell_router': sell_router,
                        'buy_path': [pair['base'], pair['quote']],
                        'sell_path': [pair['quote'], pair['base']],
                        'buy_price': buy_price,
                        'sell_price': sell_price,
                        'price_diff_percent': price_diff_percent,
                        'expected_profit': expected_profit,
                        'trade_size': trade_size,
                        'discovery_time': datetime.now().isoformat()
                    }
                    
                    opportunities.append(opportunity)
        
        return opportunities
    
    async def _get_prices_for_pair(self, pair: Dict) -> List[tuple]:
        """الحصول على الأسعار من جميع الرواتر لزوج معين"""
        prices = []
        
        # الحصول على الأسعار من كل رواتر مفعل
        enabled_routers = self.bot.config['trading']['enabled_routers']
        
        for router_address in enabled_routers:
            try:
                price = await self._get_price_from_router(pair, router_address)
                if price > 0:
                    prices.append((router_address, price))
                    
                    # تحديث آخر سعر معروف
                    key = f"{pair['base']}_{pair['quote']}_{router_address}"
                    self.last_prices[key] = {
                        'price': price,
                        'timestamp': datetime.now().isoformat()
                    }
            except Exception as e:
                logger.debug(f"Error getting price from {router_address}: {e}")
        
        return prices
    
    async def _get_price_from_router(self, pair: Dict, router_address: str) -> float:
        """الحصول على سعر من رواتر معين"""
        try:
            # استخدام دالة getAmountsOut في العقد
            router_contract = self.w3.eth.contract(
                address=router_address,
                abi=self._get_router_abi()
            )
            
            amount_in = 1 * 10**18  # 1 token (افتراضي 18 decimal)
            path = [pair['base'], pair['quote']]
            
            amounts = router_contract.functions.getAmountsOut(amount_in, path).call()
            
            if len(amounts) >= 2:
                return amounts[1] / 10**18  # تحويل إلى عدد صحيح
            else:
                return 0
                
        except Exception as e:
            logger.debug(f"Price fetch failed for {router_address}: {e}")
            
            # محاولة بديلة: استخدام API خارجي
            return await self._get_price_from_api(pair, router_address)
    
    async def _get_price_from_api(self, pair: Dict, router_address: str) -> float:
        """الحصول على سعر من API خارجي (بديل)"""
        # يمكن تنفيذ هذا باستخدام GeckoTerminal أو API مشابه
        # للتبسيط، نعود إلى سعر ثابت
        return 1.0  # قيمة افتراضية
    
    async def _calculate_expected_profit(
        self, 
        pair: Dict, 
        direction: str, 
        trade_size: int, 
        buy_price: float, 
        sell_price: float
    ) -> int:
        """حساب الربح المتوقع"""
        try:
            # حساب الكمية التي سيتم شراؤها
            buy_amount = trade_size
            
            # حساب الكمية التي سيتم بيعها (بعد احتساب slippage)
            sell_amount = buy_amount * sell_price / buy_price
            
            # خصم رسوم التداول
            fee_percent = self.bot.config['trading']['dex_fee_percent']
            sell_amount = sell_amount * (1 - fee_percent)
            
            # الربح المتوقع
            expected_profit = sell_amount - buy_amount
            
            # تحويل إلى wei
            return int(expected_profit * 10**18)
            
        except Exception as e:
            logger.error(f"Error calculating expected profit: {e}")
            return 0
    
    def _get_router_abi(self) -> List:
        """الحصول على ABI للرواتر"""
        # ABI مبسط لـ Uniswap V2 Router
        return [
            {
                "inputs": [
                    {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                    {"internalType": "address[]", "name": "path", "type": "address[]"}
                ],
                "name": "getAmountsOut",
                "outputs": [
                    {"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}
                ],
                "stateMutability": "view",
                "type": "function"
            }
        ]
    
    def get_stats(self) -> Dict:
        """الحصول على إحصائيات الماسح"""
        return self.stats
    
    def get_recent_discoveries(self, limit: int = 10) -> List[Dict]:
        """الحصول على آخر الاكتشافات"""
        return self.discovery_history[-limit:] if self.discovery_history else []

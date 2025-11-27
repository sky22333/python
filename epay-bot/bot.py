import os
import time
import logging
import asyncio
import json
import sqlite3
import aiofiles
from typing import Dict, List, Set, Optional, Tuple, Any
import threading
from datetime import datetime, timedelta
from collections import deque

import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes, ConversationHandler
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

(
    MAIN_MENU,
    WAITING_FOR_DOMAIN_INPUT,
    WAITING_FOR_MERCHANT_ID_INPUT,
    WAITING_FOR_MERCHANT_KEY_INPUT,
    WAITING_FOR_DOMAIN_CHANGE,
    WAITING_FOR_MERCHANT_ID_CHANGE,
    WAITING_FOR_MERCHANT_KEY_CHANGE,
) = range(7)

class NotificationQueue:
    """通知队列类，用于管理订单通知"""
    def __init__(self):
        self.queue = deque()
        self.processing = False
        self.lock = asyncio.Lock()
        
    async def add_notification(self, chat_id: int, order: Dict[str, Any]):
        """添加通知到队列"""
        async with self.lock:
            self.queue.append((chat_id, order))
            logger.debug(f"添加通知到队列: chat_id={chat_id}, order_id={order.get('trade_no')}")
            
    async def process_notifications(self, bot):
        """处理队列中的通知"""
        if self.processing:
            return
            
        try:
            self.processing = True
            while self.queue:
                async with self.lock:
                    if not self.queue:
                        break
                    chat_id, order = self.queue.popleft()
                
                try:
                    # 发送通知
                    money = float(order.get("money", 0))
                    time_str = order.get("endtime") or order.get("addtime") or "未知时间"
                    
                    message = (
                        f"🔔 *新订单支付成功通知*\n\n"
                        f"🔢 订单号: `{order.get('trade_no', '未知')}`\n"
                        f"💰 金额: ¥{money:.2f}\n"
                        f"⏱️ 支付时间: {time_str}\n"
                    )
                    
                    await bot.send_message(
                        chat_id=chat_id,
                        text=message,
                        parse_mode=ParseMode.MARKDOWN
                    )
                    logger.info(f"成功发送订单通知: chat_id={chat_id}, order_id={order.get('trade_no')}")
                    
                    # 发送后等待一小段时间，避免消息发送过快
                    await asyncio.sleep(1)
                    
                except Exception as e:
                    logger.error(f"发送通知失败: {str(e)}", exc_info=True)
                    # 如果发送失败，将通知重新加入队列尾部，最多重试3次
                    retries = order.get("_retries", 0) + 1
                    if retries <= 3:
                        order["_retries"] = retries
                        async with self.lock:
                            self.queue.append((chat_id, order))
                        logger.info(f"重新加入队列，重试次数: {retries}")
                        await asyncio.sleep(2)
        finally:
            self.processing = False

class SettlementNotificationQueue:
    """结算通知队列类，用于管理结算通知"""
    def __init__(self):
        self.queue = deque()
        self.processing = False
        self.lock = asyncio.Lock()
        
    async def add_notification(self, chat_id: int, settlement: Dict[str, Any]):
        """添加结算通知到队列"""
        async with self.lock:
            self.queue.append((chat_id, settlement))
            logger.debug(f"添加结算通知到队列: chat_id={chat_id}, settlement_id={settlement.get('id')}")
            
    async def process_notifications(self, bot):
        """处理队列中的结算通知"""
        if self.processing:
            return
            
        try:
            self.processing = True
            while self.queue:
                async with self.lock:
                    if not self.queue:
                        break
                    chat_id, settlement = self.queue.popleft()
                
                try:
                    # 发送通知
                    money = float(settlement.get("money", 0))
                    realmoney = float(settlement.get("realmoney", 0))
                    time_str = settlement.get("endtime") or settlement.get("addtime") or "未知时间"
                    account = settlement.get("account", "未知")
                    
                    message = (
                        f"💵 *新结算成功通知*\n\n"
                        f"🆔 结算ID: `{settlement.get('id', '未知')}`\n"
                        f"💰 结算金额: ¥{money:.2f}\n"
                        f"💸 实际金额: ¥{realmoney:.2f}\n"
                        f"👤 账户: `{account}`\n"
                        f"⏱️ 结算时间: {time_str}\n"
                    )
                    
                    await bot.send_message(
                        chat_id=chat_id,
                        text=message,
                        parse_mode=ParseMode.MARKDOWN
                    )
                    logger.info(f"成功发送结算通知: chat_id={chat_id}, settlement_id={settlement.get('id')}")
                    
                    # 发送后等待一小段时间，避免消息发送过快
                    await asyncio.sleep(1)
                    
                except Exception as e:
                    logger.error(f"发送结算通知失败: {str(e)}", exc_info=True)
                    # 如果发送失败，将通知重新加入队列尾部，最多重试3次
                    retries = settlement.get("_retries", 0) + 1
                    if retries <= 3:
                        settlement["_retries"] = retries
                        async with self.lock:
                            self.queue.append((chat_id, settlement))
                        logger.info(f"重新加入队列，重试次数: {retries}")
                        await asyncio.sleep(2)
        finally:
            self.processing = False

class OrderDatabase:
    """订单数据库类，用于持久化存储已通知的订单"""
    def __init__(self, db_path: str = "epay.db"):
        self.db_path = db_path
        self.init_db()
        
    def init_db(self):
        """初始化数据库"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 创建已通知订单表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS notified_orders (
            trade_no TEXT PRIMARY KEY,
            chat_id INTEGER,
            notified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # 创建已通知结算表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS notified_settlements (
            settlement_id TEXT PRIMARY KEY,
            chat_id INTEGER,
            notified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # 创建商户信息表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS merchant_info (
            chat_id INTEGER PRIMARY KEY,
            domain TEXT,
            pid TEXT,
            key TEXT
        )
        ''')
        
        # 创建轮询状态表
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS polling_status (
            chat_id INTEGER PRIMARY KEY,
            active INTEGER DEFAULT 0,
            last_poll TIMESTAMP
        )
        ''')
        
        conn.commit()
        conn.close()
        logger.info("数据库初始化完成")
        
    def is_order_notified(self, trade_no: str, chat_id: int) -> bool:
        """检查订单是否已通知"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT 1 FROM notified_orders WHERE trade_no = ? AND chat_id = ?", 
            (trade_no, chat_id)
        )
        result = cursor.fetchone() is not None
        
        conn.close()
        return result
        
    def mark_order_notified(self, trade_no: str, chat_id: int):
        """标记订单为已通知"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute(
            "INSERT OR REPLACE INTO notified_orders (trade_no, chat_id) VALUES (?, ?)",
            (trade_no, chat_id)
        )
        
        conn.commit()
        conn.close()
        
    def is_settlement_notified(self, settlement_id: str, chat_id: int) -> bool:
        """检查结算是否已通知"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT 1 FROM notified_settlements WHERE settlement_id = ? AND chat_id = ?", 
            (settlement_id, chat_id)
        )
        result = cursor.fetchone() is not None
        
        conn.close()
        return result
        
    def mark_settlement_notified(self, settlement_id: str, chat_id: int):
        """标记结算为已通知"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute(
            "INSERT OR REPLACE INTO notified_settlements (settlement_id, chat_id) VALUES (?, ?)",
            (settlement_id, chat_id)
        )
        
        conn.commit()
        conn.close()
        
    def get_all_notified_orders(self, chat_id: int) -> List[str]:
        """获取所有已通知的订单ID"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT trade_no FROM notified_orders WHERE chat_id = ?", (chat_id,))
        result = [row[0] for row in cursor.fetchall()]
        
        conn.close()
        return result
        
    def get_all_notified_settlements(self, chat_id: int) -> List[str]:
        """获取所有已通知的结算ID"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT settlement_id FROM notified_settlements WHERE chat_id = ?", (chat_id,))
        result = [row[0] for row in cursor.fetchall()]
        
        conn.close()
        return result
        
    def save_merchant_info(self, chat_id: int, domain: str, pid: str, key: str):
        """保存商户信息"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute(
            "INSERT OR REPLACE INTO merchant_info (chat_id, domain, pid, key) VALUES (?, ?, ?, ?)",
            (chat_id, domain, pid, key)
        )
        
        conn.commit()
        conn.close()
        
    def get_merchant_info(self, chat_id: int) -> Dict[str, str]:
        """获取商户信息"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT domain, pid, key FROM merchant_info WHERE chat_id = ?", (chat_id,))
        row = cursor.fetchone()
        
        conn.close()
        
        if row:
            return {"domain": row[0], "pid": row[1], "key": row[2]}
        return {}
        
    def get_all_merchant_info(self) -> Dict[int, Dict[str, str]]:
        """获取所有商户信息"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT chat_id, domain, pid, key FROM merchant_info")
        rows = cursor.fetchall()
        
        conn.close()
        
        result = {}
        for row in rows:
            result[row[0]] = {"domain": row[1], "pid": row[2], "key": row[3]}
        return result
        
    def set_polling_status(self, chat_id: int, active: bool):
        """设置轮询状态"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute(
            "INSERT OR REPLACE INTO polling_status (chat_id, active, last_poll) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (chat_id, 1 if active else 0)
        )
        
        conn.commit()
        conn.close()
        
    def get_polling_status(self, chat_id: int) -> bool:
        """获取轮询状态"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT active FROM polling_status WHERE chat_id = ?", (chat_id,))
        row = cursor.fetchone()
        
        conn.close()
        
        if row:
            return bool(row[0])
        return False
        
    def update_last_poll_time(self, chat_id: int):
        """更新最后轮询时间"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute(
            "UPDATE polling_status SET last_poll = CURRENT_TIMESTAMP WHERE chat_id = ?",
            (chat_id,)
        )
        
        conn.commit()
        conn.close()
        
    def get_all_active_polling(self) -> List[int]:
        """获取所有活跃的轮询chat_id"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT chat_id FROM polling_status WHERE active = 1")
        result = [row[0] for row in cursor.fetchall()]
        
        conn.close()
        return result
        
    def clean_old_records(self, days: int = 15):
        """清理旧记录"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute(
            "DELETE FROM notified_orders WHERE notified_at < datetime('now', ?)",
            (f'-{days} days',)
        )
        
        cursor.execute(
            "DELETE FROM notified_settlements WHERE notified_at < datetime('now', ?)",
            (f'-{days} days',)
        )
        
        conn.commit()
        conn.close()
        logger.info(f"已清理 {days} 天前的通知记录")

class PaymentBot:
    def __init__(self, token: str):
        """初始化支付查询机器人"""
        self.token = token
        self.db = OrderDatabase()
        self.notification_queue = NotificationQueue()
        self.settlement_notification_queue = SettlementNotificationQueue()
        
        # 从数据库加载商户信息
        self.merchant_info = self.db.get_all_merchant_info()
        
        # 轮询相关
        self.polling_tasks = {}  # 存储轮询任务 {chat_id: task}
        self.polling_active = {}  # 存储轮询状态 {chat_id: bool}
        
        # 加载轮询状态
        for chat_id in self.merchant_info:
            self.polling_active[chat_id] = self.db.get_polling_status(chat_id)
        
        # 轮询间隔管理
        self.polling_intervals = {}  # {chat_id: seconds}
        self.last_order_times = {}  # {chat_id: timestamp}
        self.last_settlement_times = {}  # {chat_id: timestamp}
        
        # 初始化应用
        self.application = Application.builder().token(token).build()
        self.setup_handlers()
        
        # 启动通知处理任务
        self.notification_task = None
        self.settlement_notification_task = None
        
        # 启动定期清理任务
        self.cleanup_task = None
        
        # 控制运行状态的标志
        self.running = False
        
        logger.info("PaymentBot初始化完成")
        
    def setup_handlers(self):
        """设置所有的命令和消息处理器"""
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler("start", self.start_command)],
            states={
                MAIN_MENU: [
                    CallbackQueryHandler(self.start_merchant_setup, pattern="^enter_credentials$"),
                    CallbackQueryHandler(self.modify_merchant_info, pattern="^modify_merchant_info$"),
                    CallbackQueryHandler(self.modify_domain, pattern="^modify_domain$"),
                    CallbackQueryHandler(self.modify_merchant_id, pattern="^modify_merchant_id$"),
                    CallbackQueryHandler(self.modify_merchant_key, pattern="^modify_merchant_key$"),
                    CallbackQueryHandler(self.check_all_orders, pattern="^check_all_orders$"),
                    CallbackQueryHandler(self.check_success_orders, pattern="^check_success_orders$"),
                    CallbackQueryHandler(self.check_settlements, pattern="^check_settlements$"),
                    CallbackQueryHandler(self.toggle_polling, pattern="^toggle_polling$"),
                    # 添加返回主菜单的回调处理
                    CallbackQueryHandler(self.back_to_main, pattern="^back_to_main$"),
                ],
                WAITING_FOR_DOMAIN_INPUT: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_domain_input)
                ],
                WAITING_FOR_MERCHANT_ID_INPUT: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_merchant_id_input)
                ],
                WAITING_FOR_MERCHANT_KEY_INPUT: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_merchant_key_input)
                ],
                WAITING_FOR_DOMAIN_CHANGE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_domain_change)
                ],
                WAITING_FOR_MERCHANT_ID_CHANGE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_merchant_id_change)
                ],
                WAITING_FOR_MERCHANT_KEY_CHANGE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_merchant_key_change)
                ],
            },
            fallbacks=[CommandHandler("cancel", self.cancel)],
        )

        self.application.add_handler(conv_handler)
        self.application.add_handler(CommandHandler("menu", self.show_menu))
        self.application.add_handler(CommandHandler("help", self.help_command))
        logger.info("处理器设置完成")
        
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """处理 /start 命令"""
        chat_id = update.effective_chat.id
        logger.info(f"用户 {chat_id} 发送了 /start 命令")
        
        # 初始化用户的临时数据存储
        if not hasattr(context, 'user_data'):
            context.user_data = {}
        
        # 显示欢迎信息和主菜单
        merchant_info = self.get_merchant_info_text(chat_id)
        welcome_text = "👋 欢迎使用易支付订单通知机器人！"
        
        if merchant_info:
            welcome_text += f"\n\n{merchant_info}"
            
        await update.message.reply_text(
            welcome_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=self.get_main_menu_keyboard(chat_id)
        )
        return MAIN_MENU
        
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """处理 /help 命令，显示帮助信息"""
        chat_id = update.effective_chat.id
        logger.info(f"用户 {chat_id} 发送了 /help 命令")
        
        help_text = (
            "📌 *支付查询机器人使用帮助*\n\n"
            "基本命令：\n"
            "/start - 启动机器人并显示主菜单\n"
            "/menu - 显示主菜单\n"
            "/help - 显示此帮助信息\n"
            "/cancel - 取消当前操作\n\n"
            "基本设置：\n"
            "1. 首先设置商户信息（域名、商户ID和密钥）\n"
            "2. 设置完成后可以随时修改商户信息\n\n"
            "功能说明：\n"
            "- 查询订单：可查看最近30条订单或仅成功订单\n"
            "- 查询结算：可查看最近结算记录\n"
            "- 长轮询：开启后自动通知新的成功支付订单和结算记录"
        )
        await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)
    
    async def show_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """显示主菜单"""
        chat_id = update.effective_chat.id
        logger.info(f"用户 {chat_id} 发送了 /menu 命令")
        
        # 获取商户信息文本
        merchant_info = self.get_merchant_info_text(chat_id)
        menu_text = "📋 主菜单 - 请选择一个操作："
        
        if merchant_info:
            menu_text = f"{merchant_info}\n\n{menu_text}"
            
        await update.message.reply_text(
            menu_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=self.get_main_menu_keyboard(chat_id)
        )
        return MAIN_MENU
    
    # 添加返回主菜单的回调处理函数
    async def back_to_main(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """处理返回主菜单的回调"""
        query = update.callback_query
        chat_id = query.message.chat_id
        logger.info(f"用户 {chat_id} 请求返回主菜单")
        
        await query.answer()
        
        # 获取商户信息文本
        merchant_info = self.get_merchant_info_text(chat_id)
        menu_text = "📋 主菜单 - 请选择一个操作："
        
        if merchant_info:
            menu_text = f"{merchant_info}\n\n{menu_text}"
            
        await query.edit_message_text(
            menu_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=self.get_main_menu_keyboard(chat_id)
        )
        return MAIN_MENU
    
    def get_merchant_info_text(self, chat_id: int) -> str:
        """获取格式化的商户信息文本，包括脱敏的密钥"""
        if chat_id in self.merchant_info and self.merchant_info[chat_id].get('pid') and self.merchant_info[chat_id].get('key'):
            merchant_data = self.merchant_info[chat_id]
            domain = merchant_data.get('domain', '未设置')
            pid = merchant_data.get('pid', '未设置')
            
            # 对密钥进行脱敏处理，只显示前几位，后8位用*代替
            key = merchant_data.get('key', '')
            if len(key) > 8:
                masked_key = key[:-8] + '********'
            else:
                masked_key = '********'
                
            return (
                "🔐 *当前商户信息*\n"
                f"🌐 域名: `{domain}`\n"
                f"🆔 商户ID: `{pid}`\n"
                f"🔑 密钥: `{masked_key}`"
            )
        return ""
    
    def get_main_menu_keyboard(self, chat_id: int) -> InlineKeyboardMarkup:
        """获取主菜单键盘"""
        # 检查是否已设置商户信息
        has_merchant_info = chat_id in self.merchant_info and self.merchant_info[chat_id].get('pid') and self.merchant_info[chat_id].get('key')
        
        # 获取轮询状态
        polling_active = self.polling_active.get(chat_id, False)
        polling_text = "🔄 关闭订单通知" if polling_active else "🔄 开启订单通知"
        
        keyboard = []
        
        if not has_merchant_info:
            # 如果未设置商户信息，只显示设置选项
            keyboard = [
                [InlineKeyboardButton("⚙️ 设置商户信息", callback_data="enter_credentials")]
            ]
        else:
            # 如果已设置商户信息，显示完整菜单
            keyboard = [
                [InlineKeyboardButton("📊 查询最近30条订单", callback_data="check_all_orders")],
                [InlineKeyboardButton("✅ 查询成功订单", callback_data="check_success_orders")],
                [InlineKeyboardButton("💵 查询结算记录", callback_data="check_settlements")],
                [InlineKeyboardButton(polling_text, callback_data="toggle_polling")],
                [InlineKeyboardButton("⚙️ 修改商户信息", callback_data="modify_merchant_info")],
                [InlineKeyboardButton("📋 显示主菜单", callback_data="back_to_main")]
            ]
        
        return InlineKeyboardMarkup(keyboard)
    
    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """取消当前操作，返回主菜单"""
        chat_id = update.effective_chat.id
        logger.info(f"用户 {chat_id} 取消了当前操作")
        
        # 清除临时存储的数据
        if 'temp_merchant_data' in context.user_data:
            del context.user_data['temp_merchant_data']
        
        # 获取商户信息文本
        merchant_info = self.get_merchant_info_text(chat_id)
        cancel_text = "❌ 已取消当前操作。返回主菜单："
        
        if merchant_info:
            cancel_text = f"{merchant_info}\n\n{cancel_text}"
        
        await update.message.reply_text(
            cancel_text,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=self.get_main_menu_keyboard(chat_id)
        )
        return MAIN_MENU
    
    async def start_merchant_setup(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """开始设置商户信息的流程，首先请求输入域名"""
        query = update.callback_query
        chat_id = query.message.chat_id
        logger.info(f"用户 {chat_id} 开始设置商户信息")
        
        await query.answer()
        
        # 初始化临时存储
        context.user_data['temp_merchant_data'] = {}
        
        await query.edit_message_text(
            "🌐 请输入易支付域名\n"
            "例如： example.com"
        )
        return WAITING_FOR_DOMAIN_INPUT
    
    async def handle_domain_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """处理域名输入，然后请求输入商户ID"""
        chat_id = update.effective_chat.id
        domain = update.message.text.strip()
        logger.info(f"用户 {chat_id} 输入了域名: {domain}")
        
        try:
            # 验证域名格式（简单验证）
            if not "." in domain:
                logger.warning(f"用户 {chat_id} 输入的域名格式无效: {domain}")
                await update.message.reply_text(
                    "❌ 域名格式无效！请输入有效的域名。\n"
                    "例如： example.com"
                )
                return WAITING_FOR_DOMAIN_INPUT
                
            # 移除协议前缀（如果有）
            domain = domain.replace("http://", "").replace("https://", "")
            
            # 保存到临时存储
            context.user_data['temp_merchant_data']['domain'] = domain
            
            # 请求输入商户ID
            await update.message.reply_text(
                "🆔 请输入商户ID\n"
                "例如：1000"
            )
            return WAITING_FOR_MERCHANT_ID_INPUT
            
        except Exception as e:
            logger.error(f"处理域名输入时发生错误: {str(e)}", exc_info=True)
            await update.message.reply_text(f"❌ 处理域名输入时发生错误: {str(e)}")
            return WAITING_FOR_DOMAIN_INPUT
    
    async def handle_merchant_id_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """处理商户ID输入，然后请求输入密钥"""
        chat_id = update.effective_chat.id
        merchant_id = update.message.text.strip()
        logger.info(f"用户 {chat_id} 输入了商户ID: {merchant_id}")
        
        try:
            # 检查商户ID是否为数字
            if not merchant_id.isdigit():
                logger.warning(f"用户 {chat_id} 输入的商户ID不是数字: {merchant_id}")
                await update.message.reply_text("❌ 商户ID必须为数字！请重新输入。")
                return WAITING_FOR_MERCHANT_ID_INPUT
            
            # 保存到临时存储
            context.user_data['temp_merchant_data']['pid'] = merchant_id
            
            # 请求输入密钥
            await update.message.reply_text(
                "🔑 请输入商户密钥\n"
                "例如： da1b2c3d4e5f6g7h8i9j0sddsda"
            )
            return WAITING_FOR_MERCHANT_KEY_INPUT
            
        except Exception as e:
            logger.error(f"处理商户ID输入时发生错误: {str(e)}", exc_info=True)
            await update.message.reply_text(f"❌ 处理商户ID输入时发生错误: {str(e)}")
            return WAITING_FOR_MERCHANT_ID_INPUT
    
    async def handle_merchant_key_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """处理密钥输入，完成商户信息设置"""
        chat_id = update.effective_chat.id
        secret_key = update.message.text.strip()
        logger.info(f"用户 {chat_id} 输入了商户密钥")
        
        try:
            # 从临时存储获取之前输入的信息
            temp_data = context.user_data.get('temp_merchant_data', {})
            domain = temp_data.get('domain')
            merchant_id = temp_data.get('pid')
            
            if not domain or not merchant_id:
                logger.error(f"用户 {chat_id} 的临时数据不完整")
                await update.message.reply_text("❌ 设置过程出错，请重新开始设置商户信息。")
                return MAIN_MENU
            
            # 保存商户信息到内存和数据库
            if chat_id not in self.merchant_info:
                self.merchant_info[chat_id] = {}
                
            self.merchant_info[chat_id]["domain"] = domain
            self.merchant_info[chat_id]["pid"] = merchant_id
            self.merchant_info[chat_id]["key"] = secret_key
            
            # 保存到数据库
            self.db.save_merchant_info(chat_id, domain, merchant_id, secret_key)
            
            logger.info(f"用户 {chat_id} 的商户信息设置成功")
            
            # 清除临时存储
            del context.user_data['temp_merchant_data']
            
            # 对密钥进行脱敏处理
            if len(secret_key) > 8:
                masked_key = secret_key[:-8] + '********'
            else:
                masked_key = '********'
            
            # 显示设置成功信息和主菜单
            await update.message.reply_text(
                f"✅ 商户信息设置成功！\n\n"
                f"🔐 *商户信息*\n"
                f"🌐 域名: `{domain}`\n"
                f"🆔 商户ID: `{merchant_id}`\n"
                f"🔑 密钥: `{masked_key}`",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=self.get_main_menu_keyboard(chat_id)
            )
            return MAIN_MENU
            
        except Exception as e:
            logger.error(f"处理商户密钥输入时发生错误: {str(e)}", exc_info=True)
            await update.message.reply_text(f"❌ 处理商户密钥输入时发生错误: {str(e)}")
            return WAITING_FOR_MERCHANT_KEY_INPUT
    
    async def modify_merchant_info(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """显示修改商户信息的选项"""
        query = update.callback_query
        chat_id = query.message.chat_id
        logger.info(f"用户 {chat_id} 选择了修改商户信息")
        
        await query.answer()
        
        # 创建修改选项的键盘
        keyboard = [
            [InlineKeyboardButton("🌐 修改域名", callback_data="modify_domain")],
            [InlineKeyboardButton("🆔 修改商户ID", callback_data="modify_merchant_id")],
            [InlineKeyboardButton("🔑 修改密钥", callback_data="modify_merchant_key")],
            [InlineKeyboardButton("↩️ 返回主菜单", callback_data="back_to_main")]
        ]
        
        await query.edit_message_text(
            "请选择要修改的信息：",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return MAIN_MENU
    
    async def modify_domain(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """开始修改域名"""
        query = update.callback_query
        chat_id = query.message.chat_id
        logger.info(f"用户 {chat_id} 选择了修改域名")
        
        await query.answer()
        
        # 获取当前域名
        current_domain = self.merchant_info.get(chat_id, {}).get('domain', '未设置')
        
        await query.edit_message_text(
            f"🌐 当前域名: `{current_domain}`\n\n"
            f"请输入新的域名\n"
            f"例如： example.com",
            parse_mode=ParseMode.MARKDOWN
        )
        return WAITING_FOR_DOMAIN_CHANGE
    
    async def handle_domain_change(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """处理域名修改"""
        chat_id = update.effective_chat.id
        new_domain = update.message.text.strip()
        logger.info(f"用户 {chat_id} 修改了域名: {new_domain}")
        
        try:
            # 验证域名格式（简单验证）
            if not "." in new_domain:
                logger.warning(f"用户 {chat_id} 输入的域名格式无效: {new_domain}")
                await update.message.reply_text(
                    "❌ 域名格式无效！请输入有效的域名。\n"
                    "例如： example.com"
                )
                return WAITING_FOR_DOMAIN_CHANGE
            
            # 移除协议前缀（如果有）
            new_domain = new_domain.replace("http://", "").replace("https://", "")
            
            # 更新商户信息
            if chat_id in self.merchant_info:
                self.merchant_info[chat_id]["domain"] = new_domain
                
                # 更新数据库
                merchant_data = self.merchant_info[chat_id]
                self.db.save_merchant_info(
                    chat_id, 
                    new_domain, 
                    merchant_data.get("pid", ""), 
                    merchant_data.get("key", "")
                )
                
                logger.info(f"用户 {chat_id} 的域名已更新")
                
                # 获取更新后的商户信息文本
                merchant_info = self.get_merchant_info_text(chat_id)
                
                # 显示更新成功信息和主菜单
                await update.message.reply_text(
                    f"✅ 域名已更新！\n\n{merchant_info}",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=self.get_main_menu_keyboard(chat_id)
                )
            else:
                logger.warning(f"用户 {chat_id} 尝试修改域名，但未找到商户信息")
                await update.message.reply_text(
                    "❌ 未找到商户信息！请先设置商户信息。",
                    reply_markup=self.get_main_menu_keyboard(chat_id)
                )
                
            return MAIN_MENU
            
        except Exception as e:
            logger.error(f"处理域名修改时发生错误: {str(e)}", exc_info=True)
            await update.message.reply_text(f"❌ 处理域名修改时发生错误: {str(e)}")
            return WAITING_FOR_DOMAIN_CHANGE
    
    async def modify_merchant_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """开始修改商户ID"""
        query = update.callback_query
        chat_id = query.message.chat_id
        logger.info(f"用户 {chat_id} 选择了修改商户ID")
        
        await query.answer()
        
        # 获取当前商户ID
        current_merchant_id = self.merchant_info.get(chat_id, {}).get('pid', '未设置')
        
        await query.edit_message_text(
            f"🆔 当前商户ID: `{current_merchant_id}`\n\n"
            f"请输入新的商户ID\n"
            f"例如：1000",
            parse_mode=ParseMode.MARKDOWN
        )
        return WAITING_FOR_MERCHANT_ID_CHANGE
    
    async def handle_merchant_id_change(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """处理商户ID修改"""
        chat_id = update.effective_chat.id
        new_merchant_id = update.message.text.strip()
        logger.info(f"用户 {chat_id} 修改了商户ID: {new_merchant_id}")
        
        try:
            # 检查商户ID是否为数字
            if not new_merchant_id.isdigit():
                logger.warning(f"用户 {chat_id} 输入的商户ID不是数字: {new_merchant_id}")
                await update.message.reply_text("❌ 商户ID必须为数字！请重新输入。")
                return WAITING_FOR_MERCHANT_ID_CHANGE
            
            # 更新商户信息
            if chat_id in self.merchant_info:
                self.merchant_info[chat_id]["pid"] = new_merchant_id
                
                # 更新数据库
                merchant_data = self.merchant_info[chat_id]
                self.db.save_merchant_info(
                    chat_id, 
                    merchant_data.get("domain", ""), 
                    new_merchant_id, 
                    merchant_data.get("key", "")
                )
                
                logger.info(f"用户 {chat_id} 的商户ID已更新")
                
                # 获取更新后的商户信息文本
                merchant_info = self.get_merchant_info_text(chat_id)
                
                # 显示更新成功信息和主菜单
                await update.message.reply_text(
                    f"✅ 商户ID已更新！\n\n{merchant_info}",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=self.get_main_menu_keyboard(chat_id)
                )
            else:
                logger.warning(f"用户 {chat_id} 尝试修改商户ID，但未找到商户信息")
                await update.message.reply_text(
                    "❌ 未找到商户信息！请先设置商户信息。",
                    reply_markup=self.get_main_menu_keyboard(chat_id)
                )
                
            return MAIN_MENU
            
        except Exception as e:
            logger.error(f"处理商户ID修改时发生错误: {str(e)}", exc_info=True)
            await update.message.reply_text(f"❌ 处理商户ID修改时发生错误: {str(e)}")
            return WAITING_FOR_MERCHANT_ID_CHANGE
    
    async def modify_merchant_key(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """开始修改商户密钥"""
        query = update.callback_query
        chat_id = query.message.chat_id
        logger.info(f"用户 {chat_id} 选择了修改商户密钥")
        
        await query.answer()
        
        # 获取当前密钥（脱敏处理）
        current_key = self.merchant_info.get(chat_id, {}).get('key', '')
        if len(current_key) > 8:
            masked_key = current_key[:-8] + '********'
        else:
            masked_key = '********'
        
        await query.edit_message_text(
            f"🔑 当前密钥: `{masked_key}`\n\n"
            f"请输入新的商户密钥\n"
            f"例如：da1b2c3d4e5f6g7h8i9j0saddas",
            parse_mode=ParseMode.MARKDOWN
        )
        return WAITING_FOR_MERCHANT_KEY_CHANGE
    
    async def handle_merchant_key_change(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """处理商户密钥修改"""
        chat_id = update.effective_chat.id
        new_key = update.message.text.strip()
        logger.info(f"用户 {chat_id} 修改了商户密钥")
        
        try:
            # 更新商户信息
            if chat_id in self.merchant_info:
                self.merchant_info[chat_id]["key"] = new_key
                
                # 更新数据库
                merchant_data = self.merchant_info[chat_id]
                self.db.save_merchant_info(
                    chat_id, 
                    merchant_data.get("domain", ""), 
                    merchant_data.get("pid", ""), 
                    new_key
                )
                
                logger.info(f"用户 {chat_id} 的商户密钥已更新")
                
                # 获取更新后的商户信息文本（包含脱敏的密钥）
                merchant_info = self.get_merchant_info_text(chat_id)
                
                # 显示更新成功信息和主菜单
                await update.message.reply_text(
                    f"✅ 商户密钥已更新！\n\n{merchant_info}",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=self.get_main_menu_keyboard(chat_id)
                )
            else:
                logger.warning(f"用户 {chat_id} 尝试修改商户密钥，但未找到商户信息")
                await update.message.reply_text(
                    "❌ 未找到商户信息！请先设置商户信息。",
                    reply_markup=self.get_main_menu_keyboard(chat_id)
                )
                
            return MAIN_MENU
            
        except Exception as e:
            logger.error(f"处理商户密钥修改时发生错误: {str(e)}", exc_info=True)
            await update.message.reply_text(f"❌ 处理商户密钥修改时发生错误: {str(e)}")
            return WAITING_FOR_MERCHANT_KEY_CHANGE
            
    async def toggle_polling(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """切换长轮询状态 - 使用自定义轮询机制，不依赖job_queue"""
        query = update.callback_query
        await query.answer()
        chat_id = query.message.chat_id
        logger.info(f"用户 {chat_id} 请求切换订单通知状态")
        
        try:
            # 检查商户信息是否已设置
            merchant_data = self.merchant_info.get(chat_id)
            if not merchant_data or not merchant_data.get("pid") or not merchant_data.get("key"):
                logger.warning(f"用户 {chat_id} 未设置商户信息")
                await query.edit_message_text(
                    "❌ 未找到商户信息！请先设置商户信息。",
                    reply_markup=self.get_main_menu_keyboard(chat_id)
                )
                return MAIN_MENU
            
            # 获取当前轮询状态
            polling_active = self.polling_active.get(chat_id, False)
            logger.debug(f"用户 {chat_id} 当前轮询状态: {polling_active}")
            
            if polling_active:
                # 停止轮询任务
                if chat_id in self.polling_tasks:
                    self.polling_tasks[chat_id].cancel()
                    del self.polling_tasks[chat_id]
                    
                # 更新状态
                self.polling_active[chat_id] = False
                self.db.set_polling_status(chat_id, False)
                
                logger.info(f"用户 {chat_id} 的轮询任务已停止")
                
                await query.edit_message_text(
                    "✅ 订单通知已关闭！\n\n"
                    "您将不再收到新订单和结算的自动通知。",
                    reply_markup=self.get_main_menu_keyboard(chat_id)
                )
            else:
                # 启动轮询任务
                self.polling_active[chat_id] = True
                self.db.set_polling_status(chat_id, True)
                
                # 初始化轮询间隔和最后订单时间
                self.polling_intervals[chat_id] = 10  # 初始轮询间隔10秒
                self.last_order_times[chat_id] = time.time()
                self.last_settlement_times[chat_id] = time.time()
                
                # 创建并启动轮询任务
                task = asyncio.create_task(self.polling_loop(chat_id))
                self.polling_tasks[chat_id] = task
                
                logger.info(f"用户 {chat_id} 的轮询任务已启动")
                
                await query.edit_message_text(
                    "✅ 订单通知已开启！\n\n"
                    "您将自动收到新的成功支付订单和结算的通知。",
                    reply_markup=self.get_main_menu_keyboard(chat_id)
                )
                
            return MAIN_MENU
                
        except Exception as e:
            logger.error(f"切换轮询状态时发生错误: {str(e)}", exc_info=True)
            await query.edit_message_text(
                f"❌ 切换通知状态时发生错误: {str(e)}",
                reply_markup=self.get_main_menu_keyboard(chat_id)
            )
            return MAIN_MENU
    
    async def polling_loop(self, chat_id: int):
        """轮询循环 - 定期检查新订单和结算"""
        logger.info(f"用户 {chat_id} 的轮询循环已启动")
        
        # 初始化错误计数和最大连续错误次数
        consecutive_errors = 0
        max_errors = 5
        
        try:
            while self.polling_active.get(chat_id, False):
                try:
                    # 获取商户信息
                    merchant_data = self.merchant_info.get(chat_id, {})
                    if not merchant_data or not merchant_data.get("pid") or not merchant_data.get("key"):
                        logger.warning(f"轮询任务: 用户 {chat_id} 未设置商户信息")
                        await asyncio.sleep(60)  # 如果没有商户信息，等待较长时间
                        continue
                    
                    # 更新数据库中的最后轮询时间
                    self.db.update_last_poll_time(chat_id)
                    
                    # 获取订单数据
                    orders = await self.get_orders(
                        merchant_data["pid"], 
                        merchant_data["key"], 
                        merchant_data["domain"]
                    )
                    
                    # 获取结算数据
                    settlements = await self.get_settlements(
                        merchant_data["pid"], 
                        merchant_data["key"], 
                        merchant_data["domain"]
                    )
                    
                    # 处理订单通知
                    if orders:
                        # 筛选出新的成功支付订单
                        new_success_orders = []
                        for order in orders:
                            trade_no = order.get("trade_no", "")
                            if (trade_no and 
                                int(order.get("status", 0)) == 1 and 
                                not self.db.is_order_notified(trade_no, chat_id)):
                                new_success_orders.append(order)
                                # 标记订单为已通知
                                self.db.mark_order_notified(trade_no, chat_id)
                        
                        if new_success_orders:
                            logger.info(f"轮询任务: 发现 {len(new_success_orders)} 条新的成功支付订单")
                            
                            # 更新上次发现订单的时间
                            self.last_order_times[chat_id] = time.time()
                            
                            # 如果发现新订单，缩短轮询间隔
                            self.polling_intervals[chat_id] = max(5, self.polling_intervals[chat_id] // 2)
                            logger.debug(f"发现新订单，缩短轮询间隔至 {self.polling_intervals[chat_id]} 秒")
                                
                            # 将新订单添加到通知队列
                            for order in new_success_orders:
                                await self.notification_queue.add_notification(chat_id, order)
                            
                            # 触发通知处理
                            asyncio.create_task(self.notification_queue.process_notifications(self.application.bot))
                    
                    # 处理结算通知
                    if settlements:
                        # 筛选出新的成功结算记录
                        new_success_settlements = []
                        for settlement in settlements:
                            settlement_id = settlement.get("id", "")
                            if (settlement_id and 
                                int(settlement.get("status", 0)) == 1 and 
                                not self.db.is_settlement_notified(settlement_id, chat_id)):
                                new_success_settlements.append(settlement)
                                # 标记结算为已通知
                                self.db.mark_settlement_notified(settlement_id, chat_id)
                        
                        if new_success_settlements:
                            logger.info(f"轮询任务: 发现 {len(new_success_settlements)} 条新的成功结算记录")
                            
                            # 更新上次发现结算的时间
                            self.last_settlement_times[chat_id] = time.time()
                            
                            # 如果发现新结算，缩短轮询间隔
                            self.polling_intervals[chat_id] = max(5, self.polling_intervals[chat_id] // 2)
                            logger.debug(f"发现新结算，缩短轮询间隔至 {self.polling_intervals[chat_id]} 秒")
                                
                            # 将新结算添加到通知队列
                            for settlement in new_success_settlements:
                                await self.settlement_notification_queue.add_notification(chat_id, settlement)
                            
                            # 触发通知处理
                            asyncio.create_task(self.settlement_notification_queue.process_notifications(self.application.bot))
                    
                    # 如果长时间没有新订单和结算，逐渐增加轮询间隔，最大30秒
                    if not new_success_orders and not new_success_settlements:
                        time_since_last_order = time.time() - self.last_order_times.get(chat_id, 0)
                        time_since_last_settlement = time.time() - self.last_settlement_times.get(chat_id, 0)
                        if time_since_last_order > 300 and time_since_last_settlement > 300:  # 5分钟没有新订单和结算
                            self.polling_intervals[chat_id] = min(30, self.polling_intervals[chat_id] + 5)
                            logger.debug(f"长时间无新订单和结算，增加轮询间隔至 {self.polling_intervals[chat_id]} 秒")
                    
                    # 重置错误计数
                    consecutive_errors = 0
                
                except Exception as e:
                    logger.error(f"轮询任务执行出错: {str(e)}", exc_info=True)
                    consecutive_errors += 1
                    
                    # 如果连续错误次数过多，增加轮询间隔
                    if consecutive_errors >= max_errors:
                        self.polling_intervals[chat_id] = min(60, self.polling_intervals[chat_id] * 2)
                        logger.warning(f"连续错误次数过多，增加轮询间隔至 {self.polling_intervals[chat_id]} 秒")
                        consecutive_errors = 0
                
                # 使用动态轮询间隔
                current_interval = self.polling_intervals[chat_id]
                logger.debug(f"用户 {chat_id} 的当前轮询间隔: {current_interval} 秒")
                await asyncio.sleep(current_interval)
                
        except asyncio.CancelledError:
            logger.info(f"用户 {chat_id} 的轮询任务被取消")
        except Exception as e:
            logger.error(f"轮询循环发生未知错误: {str(e)}", exc_info=True)
        finally:
            # 确保轮询状态被正确设置为False
            self.polling_active[chat_id] = False
            self.db.set_polling_status(chat_id, False)
            logger.info(f"用户 {chat_id} 的轮询循环已结束")
    
    async def get_orders(self, pid: str, key: str, domain: str) -> List[Dict]:
        """获取订单数据 - 增强版本，使用多种方法尝试获取数据"""
        try:
            # 构建API URL
            url = f"https://{domain}/api.php?act=orders&pid={pid}&key={key}&limit=50"
            logger.debug(f"API请求URL: {url}")
            
            # 使用aiohttp发送GET请求
            async with aiohttp.ClientSession() as session:
                try:
                    # 设置超时和重试
                    for attempt in range(3):  # 最多尝试3次
                        try:
                            async with session.get(url, timeout=15) as response:
                                logger.debug(f"API响应状态码: {response.status}")
                                
                                if response.status == 200:
                                    response_text = await response.text()
                                    logger.debug(f"API响应内容: {response_text[:200]}...")  # 只记录前200个字符
                                    
                                    try:
                                        data = json.loads(response_text)
                                        logger.debug(f"API响应解析结果: code={data.get('code')}, msg={data.get('msg')}")
                                        
                                        if data.get('code') == 1 and data.get('data'):
                                            logger.info(f"成功获取到 {len(data['data'])} 条订单数据")
                                            return data['data']
                                        else:
                                            logger.warning(f"API返回错误或无数据: {data}")
                                    except json.JSONDecodeError as e:
                                        logger.error(f"JSON解析错误: {e}, 响应内容: {response_text[:200]}...")
                                else:
                                    logger.warning(f"API请求失败，状态码: {response.status}")
                            
                            # 如果执行到这里，说明请求完成但未获取到有效数据，尝试下一次
                            await asyncio.sleep(1)
                        except asyncio.TimeoutError:
                            logger.warning(f"API请求超时，尝试次数: {attempt+1}/3")
                            await asyncio.sleep(2)  # 超时后等待更长时间再重试
                        except Exception as e:
                            logger.error(f"API请求出错: {str(e)}", exc_info=True)
                            await asyncio.sleep(2)
                except Exception as e:
                    logger.error(f"aiohttp会话出错: {str(e)}", exc_info=True)
                        
            # 如果aiohttp方法失败，尝试使用curl命令作为备用方法
            logger.info("尝试使用curl命令获取订单数据")
            try:
                curl_command = [
                    'curl', '-s', '--connect-timeout', '10', '--max-time', '15',
                    f"https://{domain}/api.php?act=orders&pid={pid}&key={key}&limit=50"
                ]
                
                process = await asyncio.create_subprocess_exec(
                    *curl_command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                
                stdout, stderr = await process.communicate()
                
                if process.returncode == 0:
                    response_text = stdout.decode('utf-8')
                    logger.debug(f"Curl响应内容: {response_text[:200]}...")
                    
                    try:
                        data = json.loads(response_text)
                        logger.debug(f"Curl响应解析结果: code={data.get('code')}, msg={data.get('msg')}")
                        
                        if data.get('code') == 1 and data.get('data'):
                            logger.info(f"使用curl成功获取到 {len(data['data'])} 条订单数据")
                            return data['data']
                        else:
                            logger.warning(f"Curl API返回错误或无数据: {data}")
                    except json.JSONDecodeError as e:
                        logger.error(f"Curl JSON解析错误: {e}, 响应内容: {response_text[:200]}...")
                else:
                    error = stderr.decode('utf-8')
                    logger.error(f"Curl命令执行失败: {error}")
            except Exception as e:
                logger.error(f"执行curl命令出错: {str(e)}", exc_info=True)
            
            # 尝试使用Python内置的urllib作为最后的备用方法
            logger.info("尝试使用urllib获取订单数据")
            try:
                import urllib.request
                import urllib.error
                
                req = urllib.request.Request(url)
                try:
                    with urllib.request.urlopen(req, timeout=15) as response:
                        response_text = response.read().decode('utf-8')
                        logger.debug(f"Urllib响应内容: {response_text[:200]}...")
                        
                        try:
                            data = json.loads(response_text)
                            logger.debug(f"Urllib响应解析结果: code={data.get('code')}, msg={data.get('msg')}")
                            
                            if data.get('code') == 1 and data.get('data'):
                                logger.info(f"使用urllib成功获取到 {len(data['data'])} 条订单数据")
                                return data['data']
                            else:
                                logger.warning(f"Urllib API返回错误或无数据: {data}")
                        except json.JSONDecodeError as e:
                            logger.error(f"Urllib JSON解析错误: {e}, 响应内容: {response_text[:200]}...")
                except urllib.error.URLError as e:
                    logger.error(f"Urllib请求失败: {str(e)}")
            except Exception as e:
                logger.error(f"使用urllib出错: {str(e)}", exc_info=True)
            
            logger.warning("所有方法都未能获取到订单数据，返回空列表")
            return []
        except Exception as e:
            logger.error(f"获取订单时发生未知错误: {e}", exc_info=True)
            return []
    
    async def get_settlements(self, pid: str, key: str, domain: str) -> List[Dict]:
        """获取结算数据 - 使用多种方法尝试获取数据"""
        try:
            # 构建API URL
            url = f"https://{domain}/api.php?act=settle&pid={pid}&key={key}"
            logger.debug(f"结算API请求URL: {url}")
            
            # 使用aiohttp发送GET请求
            async with aiohttp.ClientSession() as session:
                try:
                    # 设置超时和重试
                    for attempt in range(3):  # 最多尝试3次
                        try:
                            async with session.get(url, timeout=15) as response:
                                logger.debug(f"结算API响应状态码: {response.status}")
                                
                                if response.status == 200:
                                    response_text = await response.text()
                                    logger.debug(f"结算API响应内容: {response_text[:200]}...")  # 只记录前200个字符
                                    
                                    try:
                                        data = json.loads(response_text)
                                        logger.debug(f"结算API响应解析结果: code={data.get('code')}, msg={data.get('msg')}")
                                        
                                        if data.get('code') == 1 and data.get('data'):
                                            logger.info(f"成功获取到 {len(data['data'])} 条结算数据")
                                            return data['data']
                                        else:
                                            logger.warning(f"结算API返回错误或无数据: {data}")
                                    except json.JSONDecodeError as e:
                                        logger.error(f"结算JSON解析错误: {e}, 响应内容: {response_text[:200]}...")
                                else:
                                    logger.warning(f"结算API请求失败，状态码: {response.status}")
                            
                            # 如果执行到这里，说明请求完成但未获取到有效数据，尝试下一次
                            await asyncio.sleep(1)
                        except asyncio.TimeoutError:
                            logger.warning(f"结算API请求超时，尝试次数: {attempt+1}/3")
                            await asyncio.sleep(2)  # 超时后等待更长时间再重试
                        except Exception as e:
                            logger.error(f"结算API请求出错: {str(e)}", exc_info=True)
                            await asyncio.sleep(2)
                except Exception as e:
                    logger.error(f"结算aiohttp会话出错: {str(e)}", exc_info=True)
                        
            # 如果aiohttp方法失败，尝试使用curl命令作为备用方法
            logger.info("尝试使用curl命令获取结算数据")
            try:
                curl_command = [
                    'curl', '-s', '--connect-timeout', '10', '--max-time', '15',
                    f"https://{domain}/api.php?act=settle&pid={pid}&key={key}"
                ]
                
                process = await asyncio.create_subprocess_exec(
                    *curl_command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                
                stdout, stderr = await process.communicate()
                
                if process.returncode == 0:
                    response_text = stdout.decode('utf-8')
                    logger.debug(f"结算Curl响应内容: {response_text[:200]}...")
                    
                    try:
                        data = json.loads(response_text)
                        logger.debug(f"结算Curl响应解析结果: code={data.get('code')}, msg={data.get('msg')}")
                        
                        if data.get('code') == 1 and data.get('data'):
                            logger.info(f"使用curl成功获取到 {len(data['data'])} 条结算数据")
                            return data['data']
                        else:
                            logger.warning(f"结算Curl API返回错误或无数据: {data}")
                    except json.JSONDecodeError as e:
                        logger.error(f"结算Curl JSON解析错误: {e}, 响应内容: {response_text[:200]}...")
                else:
                    error = stderr.decode('utf-8')
                    logger.error(f"结算Curl命令执行失败: {error}")
            except Exception as e:
                logger.error(f"执行结算curl命令出错: {str(e)}", exc_info=True)
            
            # 尝试使用Python内置的urllib作为最后的备用方法
            logger.info("尝试使用urllib获取结算数据")
            try:
                import urllib.request
                import urllib.error
                
                req = urllib.request.Request(url)
                try:
                    with urllib.request.urlopen(req, timeout=15) as response:
                        response_text = response.read().decode('utf-8')
                        logger.debug(f"结算Urllib响应内容: {response_text[:200]}...")
                        
                        try:
                            data = json.loads(response_text)
                            logger.debug(f"结算Urllib响应解析结果: code={data.get('code')}, msg={data.get('msg')}")
                            
                            if data.get('code') == 1 and data.get('data'):
                                logger.info(f"使用urllib成功获取到 {len(data['data'])} 条结算数据")
                                return data['data']
                            else:
                                logger.warning(f"结算Urllib API返回错误或无数据: {data}")
                        except json.JSONDecodeError as e:
                            logger.error(f"结算Urllib JSON解析错误: {e}, 响应内容: {response_text[:200]}...")
                except urllib.error.URLError as e:
                    logger.error(f"结算Urllib请求失败: {str(e)}")
            except Exception as e:
                logger.error(f"使用结算urllib出错: {str(e)}", exc_info=True)
            
            logger.warning("所有方法都未能获取到结算数据，返回空列表")
            return []
        except Exception as e:
            logger.error(f"获取结算时发生未知错误: {e}", exc_info=True)
            return []
        
    async def check_all_orders(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """查询最近30条订单"""
        query = update.callback_query
        await query.answer()
        chat_id = query.message.chat_id
        logger.info(f"用户 {chat_id} 请求查询最近30条订单")
        
        try:
            # 检查商户信息是否已设置
            merchant_data = self.merchant_info.get(chat_id)
            if not merchant_data or not merchant_data.get("pid") or not merchant_data.get("key"):
                logger.warning(f"用户 {chat_id} 未设置商户信息")
                await query.edit_message_text(
                    "❌ 未找到商户信息！请先设置商户信息。",
                    reply_markup=self.get_main_menu_keyboard(chat_id)
                )
                return MAIN_MENU
            
            # 发送正在查询的提示
            await query.edit_message_text("🔍 正在查询订单，请稍候...")
            
            # 获取订单数据
            orders = await self.get_orders(
                merchant_data["pid"], 
                merchant_data["key"], 
                merchant_data["domain"]
            )
            
            if not orders:
                logger.warning(f"用户 {chat_id} 查询订单，但未找到数据")
                await self.application.bot.send_message(
                    chat_id=chat_id,
                    text="❌ 未找到订单数据！",
                    reply_markup=self.get_main_menu_keyboard(chat_id)
                )
                return MAIN_MENU
            
            # 限制最多显示30条订单
            recent_orders = orders[:30]
            
            # 格式化订单信息
            message = "📋 *最近30条订单*\n\n"
            
            for i, order in enumerate(recent_orders, 1):
                trade_no = order.get("trade_no", "未知")
                money = float(order.get("money", 0))
                status = int(order.get("status", 0))
                status_text = "✅ 已支付" if status == 1 else "❌ 未支付"
                
                # 检查时间字段
                time_str = order.get("addtime", "未知时间")
                
                message += (
                    f"*订单 {i}*\n"
                    f"🔢 订单号: `{trade_no}`\n"
                    f"💰 金额: ¥{money:.2f}\n"
                    f"📊 状态: {status_text}\n"
                    f"⏱️ 创建时间: {time_str}\n\n"
                )
            
            # 更新已知订单集合（用于检测新订单）
            for order in orders:
                trade_no = order.get("trade_no", "")
                if trade_no and int(order.get("status", 0)) == 1:
                    self.db.mark_order_notified(trade_no, chat_id)
            
            # 发送订单信息
            await self.application.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=self.get_main_menu_keyboard(chat_id)
            )
                
        except Exception as e:
            logger.error(f"查询订单时发生错误: {str(e)}", exc_info=True)
            await self.application.bot.send_message(
                chat_id=chat_id,
                text=f"❌ 查询订单时发生错误: {str(e)}",
                reply_markup=self.get_main_menu_keyboard(chat_id)
            )
            
        return MAIN_MENU
    
    async def check_success_orders(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """查询成功支付的订单"""
        query = update.callback_query
        await query.answer()
        chat_id = query.message.chat_id
        logger.info(f"用户 {chat_id} 请求查询成功支付订单")
        
        try:
            # 检查商户信息是否已设置
            merchant_data = self.merchant_info.get(chat_id)
            if not merchant_data or not merchant_data.get("pid") or not merchant_data.get("key"):
                logger.warning(f"用户 {chat_id} 未设置商户信息")
                await query.edit_message_text(
                    "❌ 未找到商户信息！请先设置商户信息。",
                    reply_markup=self.get_main_menu_keyboard(chat_id)
                )
                return MAIN_MENU
            
            # 发送正在查询的提示
            await query.edit_message_text("🔍 正在查询成功支付订单，请稍候...")
            
            # 获取订单数据
            orders = await self.get_orders(
                merchant_data["pid"], 
                merchant_data["key"], 
                merchant_data["domain"]
            )
            
            if not orders:
                logger.warning(f"用户 {chat_id} 查询成功订单，但未找到数据")
                await self.application.bot.send_message(
                    chat_id=chat_id,
                    text="❌ 未找到订单数据！",
                    reply_markup=self.get_main_menu_keyboard(chat_id)
                )
                return MAIN_MENU
            
            # 筛选出成功支付的订单
            recent_success_orders = []
            for order in orders:
                if int(order.get("status", 0)) == 1:  # 状态为1表示已支付
                    recent_success_orders.append(order)
            
            if not recent_success_orders:
                logger.warning(f"用户 {chat_id} 查询成功订单，但未找到成功支付的订单")
                await self.application.bot.send_message(
                    chat_id=chat_id,
                    text="❌ 未找到成功支付的订单！",
                    reply_markup=self.get_main_menu_keyboard(chat_id)
                )
                return MAIN_MENU
            
            # 限制最多显示20条订单
            recent_success_orders = recent_success_orders[:20]
            
            # 计算总金额
            total_amount = sum(float(order.get("money", 0)) for order in recent_success_orders)
            
            # 格式化订单信息
            message = f"✅ *成功支付订单* (共 {len(recent_success_orders)} 条)\n"
            message += f"💰 总金额: ¥{total_amount:.2f}\n\n"
            
            for i, order in enumerate(recent_success_orders, 1):
                trade_no = order.get("trade_no", "未知")
                money = float(order.get("money", 0))
                
                # 检查时间字段，优先使用支付时间
                time_str = order.get("endtime") or order.get("addtime") or "未知时间"
                
                message += (
                    f"*订单 {i}*\n"
                    f"🔢 订单号: `{trade_no}`\n"
                    f"💰 金额: ¥{money:.2f}\n"
                    f"⏱️ 支付时间: {time_str}\n\n"
                )
            
            # 更新已知订单集合（用于检测新订单）
            for order in recent_success_orders:
                trade_no = order.get("trade_no", "")
                if trade_no:
                    self.db.mark_order_notified(trade_no, chat_id)
            
            # 发送订单信息
            await self.application.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=self.get_main_menu_keyboard(chat_id)
            )
                
        except Exception as e:
            logger.error(f"查询成功订单时发生错误: {str(e)}", exc_info=True)
            await self.application.bot.send_message(
                chat_id=chat_id,
                text=f"❌ 查询成功订单时发生错误: {str(e)}",
                reply_markup=self.get_main_menu_keyboard(chat_id)
            )
            
        return MAIN_MENU
    
    async def check_settlements(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
        """查询结算记录"""
        query = update.callback_query
        await query.answer()
        chat_id = query.message.chat_id
        logger.info(f"用户 {chat_id} 请求查询结算记录")
        
        try:
            # 检查商户信息是否已设置
            merchant_data = self.merchant_info.get(chat_id)
            if not merchant_data or not merchant_data.get("pid") or not merchant_data.get("key"):
                logger.warning(f"用户 {chat_id} 未设置商户信息")
                await query.edit_message_text(
                    "❌ 未找到商户信息！请先设置商户信息。",
                    reply_markup=self.get_main_menu_keyboard(chat_id)
                )
                return MAIN_MENU
            
            # 发送正在查询的提示
            await query.edit_message_text("🔍 正在查询结算记录，请稍候...")
            
            # 获取结算数据
            settlements = await self.get_settlements(
                merchant_data["pid"], 
                merchant_data["key"], 
                merchant_data["domain"]
            )
            
            if not settlements:
                logger.warning(f"用户 {chat_id} 查询结算记录，但未找到数据")
                await self.application.bot.send_message(
                    chat_id=chat_id,
                    text="❌ 未找到结算记录！",
                    reply_markup=self.get_main_menu_keyboard(chat_id)
                )
                return MAIN_MENU
            
            # 限制最多显示20条结算记录
            recent_settlements = settlements[:20]
            
            # 计算总金额
            total_amount = sum(float(settlement.get("money", 0)) for settlement in recent_settlements)
            total_real_amount = sum(float(settlement.get("realmoney", 0)) for settlement in recent_settlements)
            
            # 格式化结算信息
            message = f"💵 *结算记录* (共 {len(recent_settlements)} 条)\n"
            message += f"💰 总金额: ¥{total_amount:.2f}\n"
            message += f"💸 实际总金额: ¥{total_real_amount:.2f}\n\n"
            
            for i, settlement in enumerate(recent_settlements, 1):
                settlement_id = settlement.get("id", "未知")
                money = float(settlement.get("money", 0))
                realmoney = float(settlement.get("realmoney", 0))
                status = int(settlement.get("status", 0))
                status_text = "✅ 已完成" if status == 1 else "❌ 未完成"
                account = settlement.get("account", "未知")
                
                # 检查时间字段，优先使用结算完成时间
                time_str = settlement.get("endtime") or settlement.get("addtime") or "未知时间"
                
                message += (
                    f"*结算 {i}*\n"
                    f"🆔 结算ID: `{settlement_id}`\n"
                    f"💰 金额: ¥{money:.2f}\n"
                    f"💸 实际金额: ¥{realmoney:.2f}\n"
                    f"👤 账户: `{account}`\n"
                    f"📊 状态: {status_text}\n"
                    f"⏱️ 时间: {time_str}\n\n"
                )
            
            # 更新已知结算集合（用于检测新结算）
            for settlement in settlements:
                settlement_id = settlement.get("id", "")
                if settlement_id and int(settlement.get("status", 0)) == 1:
                    self.db.mark_settlement_notified(settlement_id, chat_id)
            
            # 发送结算信息
            await self.application.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=self.get_main_menu_keyboard(chat_id)
            )
                
        except Exception as e:
            logger.error(f"查询结算记录时发生错误: {str(e)}", exc_info=True)
            await self.application.bot.send_message(
                chat_id=chat_id,
                text=f"❌ 查询结算记录时发生错误: {str(e)}",
                reply_markup=self.get_main_menu_keyboard(chat_id)
            )
            
        return MAIN_MENU
    
    async def start(self):
        """启动机器人"""
        logger.info("启动机器人...")
        
        # 设置运行状态标志
        self.running = True
        
        # 启动通知处理任务
        self.notification_task = asyncio.create_task(self.process_notifications_loop())
        self.settlement_notification_task = asyncio.create_task(self.process_settlement_notifications_loop())
        
        # 启动定期清理任务
        self.cleanup_task = asyncio.create_task(self.cleanup_loop())
        
        # 恢复活跃的轮询任务
        active_chat_ids = self.db.get_all_active_polling()
        for chat_id in active_chat_ids:
            if chat_id in self.merchant_info:
                logger.info(f"恢复用户 {chat_id} 的轮询任务")
                self.polling_active[chat_id] = True
                self.polling_intervals[chat_id] = 10  # 初始轮询间隔10秒
                self.last_order_times[chat_id] = time.time()
                self.last_settlement_times[chat_id] = time.time()
                self.polling_tasks[chat_id] = asyncio.create_task(self.polling_loop(chat_id))
        
        # 启动机器人
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()
        
        logger.info("机器人已启动")
        
        try:
            # 保持运行直到收到停止信号
            while self.running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("机器人任务被取消")
        finally:
            # 停止所有任务
            await self.stop()
    
    async def stop(self):
        """停止机器人"""
        logger.info("正在停止机器人...")
        
        # 设置运行状态标志
        self.running = False
        
        # 停止所有任务
        if self.notification_task:
            self.notification_task.cancel()
            try:
                await self.notification_task
            except asyncio.CancelledError:
                logger.info("通知处理循环已取消")
        
        if self.settlement_notification_task:
            self.settlement_notification_task.cancel()
            try:
                await self.settlement_notification_task
            except asyncio.CancelledError:
                logger.info("结算通知处理循环已取消")
        
        if self.cleanup_task:
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                logger.info("定期清理循环已取消")
        
        # 停止所有轮询任务
        for chat_id, task in list(self.polling_tasks.items()):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                logger.info(f"用户 {chat_id} 的轮询任务已取消")
        
        # 停止轮询
        await self.application.updater.stop()
        
        # 关闭应用
        await self.application.stop()
        await self.application.shutdown()
        
        logger.info("机器人已停止")
    
    async def process_notifications_loop(self):
        """处理通知队列的循环"""
        logger.info("启动通知处理循环")
        try:
            while self.running:
                await self.notification_queue.process_notifications(self.application.bot)
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("通知处理循环已取消")
        except Exception as e:
            logger.error(f"通知处理循环发生错误: {str(e)}", exc_info=True)
    
    async def process_settlement_notifications_loop(self):
        """处理结算通知队列的循环"""
        logger.info("启动结算通知处理循环")
        try:
            while self.running:
                await self.settlement_notification_queue.process_notifications(self.application.bot)
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("结算通知处理循环已取消")
        except Exception as e:
            logger.error(f"结算通知处理循环发生错误: {str(e)}", exc_info=True)
    
    async def cleanup_loop(self):
        """定期清理旧记录的循环"""
        logger.info("启动定期清理循环")
        try:
            while self.running:
                # 每天清理一次
                self.db.clean_old_records(days=15)
                # 等待24小时
                await asyncio.sleep(24 * 60 * 60)
        except asyncio.CancelledError:
            logger.info("定期清理循环已取消")
        except Exception as e:
            logger.error(f"定期清理循环发生错误: {str(e)}", exc_info=True)

async def main():
    """主函数"""
    # 从环境变量获取机器人令牌，如果没有则使用默认值
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
    
    # 创建并启动机器人
    bot = PaymentBot(token)
    try:
        await bot.start()
    except KeyboardInterrupt:
        logger.info("接收到键盘中断，正在停止机器人...")
    except Exception as e:
        logger.error(f"机器人运行时发生错误: {str(e)}", exc_info=True)
    finally:
        # 确保机器人正确停止
        await bot.stop()

if __name__ == "__main__":
    # 运行主函数
    asyncio.run(main())

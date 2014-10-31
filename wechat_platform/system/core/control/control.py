# -*- coding: utf-8 -*-

import random

from django.core.exceptions import ObjectDoesNotExist
from django.http.response import HttpResponse
from wechat_sdk.context.framework.django import DatabaseContextStore
from wechat_sdk.messages import EventMessage

from system.core.exceptions import WechatCriticalException, PluginException, PluginLoadError
from system.rule.models import Rule
from system.keyword.models import Keyword
from system.rule_match.models import RuleMatch
from system.request.models import RequestMessage, RequestEvent
from system.plugin.models import Plugin
from system.plugin.framework import load_plugin


class ControlCenter(object):
    """
    微信控制中心类
    """
    def __init__(self, official_account, wechat_instance):
        """
        控制中心初始化
        :param official_account: 公众号实例 (OfficialAccount)
        :param wechat_instance: 微信请求实例 (WechatBasic)
        """
        self.official_account = official_account  # 公众号实例
        self.wechat = wechat_instance  # 微信请求
        self.message = self.wechat.get_message()  # 微信消息
        self.context = DatabaseContextStore(openid=self.message.source)  # 微信上下文对话

        self.match_plugin_list = []

    def match(self):
        """
        对微信请求信息进行匹配, 设置匹配的插件标识符
        """
        # 如果当前在上下文对话模式中, 直接返回该插件iden
        context_plugin_iden = self.context.get('_plugin_iden')
        if context_plugin_iden:
            return [{
                'iden': context_plugin_iden,
                'reply_id': 0
            }, ]

        # 如果不在上下文对话模式中, 直接匹配信息类型, 然后转发给响应的详细匹配函数并返回结果
        if isinstance(self.message, EventMessage):
            func = 'match_event'
        else:
            func = 'match_' + self.message.type
        if hasattr(self, func):
            return getattr(self, func)()
        else:
            raise WechatCriticalException('no match method found')

    def match_text(self):
        """
        对文本请求信息进行匹配, 并返回匹配的插件标识符列表
        :return: 插件标识符列表, 格式描述参见 __init__ 函数
        """
        keyword = Keyword.manager.search(keyword=self.message.content)
        if not keyword:  # 当没有找到匹配关键字时返回默认回复插件
            return [{
                'iden': 'default',
                'reply_id': 0
            }, ]

        rule = keyword.rule
        rule_match = RuleMatch.manager.get(rule=rule)
        if not rule_match:  # 当该规则没有任何插件匹配可以使用时返回默认回复插件
            return [{
                'iden': 'default',
                'reply_id': 0
            }]

        # 将该规则所有的插件匹配按顺序写入列表
        plugin_list = []
        for item in rule_match:
            plugin_list.append({
                'iden': item.plugin_iden,
                'reply_id': item.reply_id
            })

        # 根据规则的返回模式返回相应的列表
        if rule.reply_pattern == Rule.REPLY_PATTERN_ALL:  # 全部回复
            return plugin_list
        elif rule.reply_pattern == Rule.REPLY_PATTERN_RANDOM:  # 随机回复
            return [random.choice(plugin_list), ]
        elif rule.reply_pattern == Rule.REPLY_PATTERN_FORWARD:  # 顺序回复
            # TODO: return the plugin list by means of response model
            raise Exception('have not yet implemented')
        elif rule.reply_pattern == Rule.REPLY_PATTERN_REVERSE:  # 逆序回复
            # TODO: return the plugin list by means of response model
            raise Exception('have not yet implemented')

    def process(self, plugin_dict, is_exclusive=False):
        """
        插件处理过程, 负责调用插件并返回执行结果
        :param plugin_dict: 插件字典, exp: {'iden': 'plugin_iden', 'reply_id': 0}
        :param is_exclusive: 插件是否可以独享该操作
        :return: 插件返回结果
        """
        iden = plugin_dict['iden']
        reply_id = plugin_dict['reply_id']

        if self._is_system_plugin(iden=iden):
            plugin = Plugin(iden=iden, name=iden)
        else:
            try:
                plugin = Plugin.objects.get(pk=iden)
            except ObjectDoesNotExist:
                raise PluginLoadError('no plugin iden found in database')

        plugin_loaded = load_plugin(
            official_account=self.official_account,
            wechat=self.wechat,
            context=self.context,
            message=self.message,
            is_exclusive=is_exclusive,
            plugin=plugin,
            reply_id=reply_id,  # 仅系统插件可用
            is_system=self._is_system_plugin(iden)
        )
        return plugin_loaded.process()

    @property
    def response(self):
        final_response = None

        # 判断请求是否重复, 如果重复则返回原响应内容, 否则保存当前请求
        if isinstance(self.message, EventMessage):
            if RequestEvent.manager.is_repeat(self.wechat):
                # TODO: return the response
                raise Exception('have not yet implemented')
            RequestEvent.manager.add(self.wechat)
        else:
            if RequestMessage.manager.is_repeat(self.wechat):
                # TODO: return the response
                raise Exception('have not yet implemented')
            RequestMessage.manager.add(self.wechat)

        self.match_plugin_list = self.match()
        if len(self.match_plugin_list) == 1:
            is_exclusive = True
        else:
            is_exclusive = False
        for plugin in self.match_plugin_list:
            try:
                result = self.process(plugin_dict=plugin, is_exclusive=is_exclusive)
                if result and is_exclusive:  # 说明该插件需要返回XML数据
                    # TODO: write the result to response model
                    final_response = result
                else:  # 说明该插件不需要返回XML数据, 已经自行处理完成, 返回空字符串即可
                    # TODO: write the result to response model
                    final_response = ''
            except PluginException:
                # TODO: write log files
                pass

        self.context.save()  # 保存所有上下文对话到数据库中
        return HttpResponse(final_response)

    def _is_system_plugin(self, iden):
        """
        根据 iden 判定是否为系统插件
        :param iden: 插件标识符
        :return: 如果为系统插件, 返回 True
        """
        system_plugin = [
            'text',
            'news',
            'music',
            'picture',
            'video',
            'voice',
            'location',
            'link',
            'default',
            'subscribe',
            'unsubscribe',
            'click',
            'view'
        ]
        if iden in system_plugin:
            return True
        else:
            return False
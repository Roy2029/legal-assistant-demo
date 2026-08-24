import logging
from datetime import datetime
import json
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)


class ContextManager:
    def __init__(self, config, llm):

        self.config = config
        self.llm = llm
        self.logger = logger

        llm_key = config.get("default_set", "deepseek")
        default_set = config.get("llm." + llm_key, {})

        self.system_prompt = self._build_system_prompt()
        self.compress_threshold = default_set.get('max_input_tokens', 64000) * 0.5
        self.recent_tool_keep = 4
        self.history_compress_round = 5

    def _read_prompt(self, path) -> str:
        p = Path(path)
        if p.exists():
            return p.read_text(encoding="utf-8")
        return ""

    def _build_system_prompt(self):

        self.logger.info("Building system prompt")
        prompt_path = Path("prompts/agent_prompt.txt")
        if prompt_path.exists():
            sys_prompt = prompt_path.read_text(encoding="utf-8")
        else:
            sys_prompt = (
                "你是一个基于知识库的 RAG 助手。"
                "请根据提供的上下文信息回答问题，"
                "如果上下文信息不足以回答问题，请如实告知。"
            )

        self.logger.info(f"System prompt built, length: {len(sys_prompt)}")
        return sys_prompt

    def build_messages(self, memory, rag_recall):

        messages = []

        # system prompt
        system_content = self.system_prompt + f"\n当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        messages.append({
            "role": "system",
            "content": system_content
        })

        # memory window
        window = self.config.get("memory.max_messages", 10)

        messages.extend(memory[-window:])

        messages.append({
            "role": "system",
            "content": rag_recall
        })

        # 阈值检测：计算总字符数
        total_chars = sum(len(msg.get("content", "")) for msg in messages)
        if total_chars > self.compress_threshold:
            self.logger.info(f"Context length {total_chars} exceeds threshold {self.compress_threshold}, triggering compression")
            # 压缩memory部分（不压缩系统提示）
            compressed_memory = self.compress_context(memory)  # 先不传递llm，由agent处理
            # 更新memory为压缩后的版本
            memory = compressed_memory
            # 重新构建messages
            messages = [{
                "role": "system",
                "content": system_content
            }]
            messages.extend(memory[-window:])
            self.logger.info(f"After compression, memory length: {len(memory)}")

        self.logger.info(f"Messages built, total messages: {len(messages)}")
        return messages

    def _compress_context(self, memory):
        """
        压缩对话历史memory
        :param memory: 原始memory列表，每条消息为dict with 'role' and 'content'
        :param llm: 可选的LLM实例，用于摘要压缩
        :return: 压缩后的memory列表
        """
        if not memory:
            return memory

        self.logger.info(f"Compressing memory of length {len(memory)}")

        # 1. 识别系统消息中的工具调用结果
        tool_pattern = re.compile(r'^Tool \[[^\]]+\] returned:')
        system_indices = []  # 系统消息的索引
        for i, msg in enumerate(memory):
            if msg.get("role") == "system":
                system_indices.append(i)

        # 2. 确定保留的工具调用：最近5条系统消息
        keep_tool_indices = set()
        recent_system = system_indices[-self.recent_tool_keep:] if len(system_indices) > self.recent_tool_keep else system_indices
        for idx in recent_system:
            content = memory[idx].get("content", "")
            if tool_pattern.match(content):
                keep_tool_indices.add(idx)

        # 3. 处理用户和助手消息：超过10轮的消息进行摘要
        compress_candidates = []  # 需要摘要的消息索引
        if len(memory) > self.history_compress_round:
            # 超过10轮，即前 len(memory)-10 条消息中的用户和助手消息
            for i in range(len(memory) - self.history_compress_round):
                msg = memory[i]
                if msg.get("role") in ["user", "assistant"]:
                    compress_candidates.append(i)

        # 4. 执行摘要压缩（如果有llm且有待摘要的消息）
        if compress_candidates:
            # 分组摘要：将连续的用户/助手消息分组
            groups = []
            current_group = []
            for i in sorted(compress_candidates):
                if not current_group or i == current_group[-1] + 1:
                    current_group.append(i)
                else:
                    groups.append(current_group)
                    current_group = [i]
            if current_group:
                groups.append(current_group)

            # 对每组进行摘要
            for group in groups:
                history_text = "\n".join([
                    f"{memory[i]['role']}: {memory[i]['content']}" for i in group
                ])
                prompt_template = self._read_prompt("prompts/compress_prompt.txt")
                if not prompt_template:
                    prompt_template = "请总结以下对话历史：\n{history}\n\n摘要："
                prompt = prompt_template.format(history=history_text)
                try:
                    resp = self.llm.generate([{"role": "system", "content": prompt}])
                    summary = resp.choices[0].message.content.strip()
                    # 创建摘要消息（作为系统消息插入）
                    summary_msg = {
                        "role": "system",
                        "content": f"[历史对话摘要] {summary}"
                    }
                    # 替换原组消息：删除原消息，插入摘要消息
                    # 注意：索引会变化，简化处理：先标记删除，最后统一处理
                    # 这里简化，直接在新列表中构建
                    pass  # 由于实现较复杂，暂不实现，先标记
                except Exception as e:
                    self.logger.error(f"LLM summary failed: {e}")
                    # 摘要失败，保留原消息


        # 5. 构建压缩后的memory
        compressed = []
        for i, msg in enumerate(memory):
            role = msg.get("role")
            content = msg.get("content", "")
            if role == "system" and tool_pattern.match(content):
                # 工具调用结果
                if i in keep_tool_indices:
                    compressed.append(msg)  # 保留
                else:
                    # 删除
                    continue
            else:
                # 用户、助手或其他系统消息保留
                compressed.append(msg)

        self.logger.info(f"Compression completed, original {len(memory)} -> compressed {len(compressed)}")
        return compressed


    def compress_context(self, messages):
        
        recent_messages = messages[-5:]
        messages = messages[:-5]
        
        groups = []          # 存储每个对话单元的消息列表
        current_group = []   # 当前正在构建的单元
        in_group = False
        
        #增加一个逻辑，若开头是历史摘要，遍历所有历史摘要，判断长度，适当分为若干组

        # 1. 按规则分组：以 user 消息开始，到下一个 user 或列表结束
        for msg in messages:
            if msg['role'] == 'user':
                if in_group:
                    # 上一个单元结束，保存
                    groups.append(current_group)
                    current_group = []
                # 开始新单元
                current_group.append(msg)
                in_group = True
            else:
                if in_group:
                    current_group.append(msg)
                # 忽略不在单元内的非 user 消息（例如开头的 system/assistant）
        
        # 处理最后一个单元
        if current_group:
            groups.append(current_group)
        
        # 2. 提取每个单元的用户内容和助手思考内容
        summary_message = []
        for group in groups:#group:[{chat},{chat}...]
            result=''
            if not group:
                continue

            for msg in group:
                if msg['role'] == 'user':
                    result+=f"user：{msg['content']}\n"
                    break
            
            for msg in group:
                if msg['role'] == 'assistant':
                    c_time = msg['time']
                    try:
                        # 解析 JSON 字符串
                        data = json.loads(msg['content'])
                        thought = data.get('thought', '')
                        if thought:
                            result+=f"assistant thought：{thought}\n"
                    except (json.JSONDecodeError, TypeError):
                        # 若解析失败，忽略该条消息（或根据需求保留原始内容）
                        print(f"assistant 历史对话解析失败：{msg['time']}")
                        #pdb.set_trace()
                        result+=f"assistant：{msg['content'][:100]}\n"
                elif msg['role'] == 'system':
                    try:
                        # 解析 JSON 字符串
                        header = msg['content'][:50].split(' ')#解析调用工具
                        if header[0]=='Tool':
                            result+=f"调用了工具：{header[1]}\n"
                        elif header[0]=='[历史摘要]':
                            result+=f"摘要：{msg['content']}\n"
                    except:
                        print(f"工具调用解析失败：{msg['time']}")
                        result+=f"调用了工具\n"

            #group_sum = {"role":"system","content":result,"time": c_time}

            prompt_template = self._read_prompt("prompts/compress_prompt.txt")
            if not prompt_template:
                prompt_template = "请总结以下对话历史：\n{history}\n\n摘要："
            prompt = prompt_template.format(history=result)
            try:
                resp = self.llm.generate([{"role": "system", "content": prompt}])
                summary = resp.choices[0].message.content.strip()
                # 创建摘要消息（作为系统消息插入）
                group_sum = {
                    "role": "system",
                    "content": f"[历史摘要] {summary}",
                    "time": c_time
                }
            except:
                print(f"历史对话总结失败：{c_time}")
                group_sum = {"role":"system","content":result,"time": c_time}

            summary_message.append(group_sum)
            print(f"总结了{len(group)}条对话，总结如下：{group_sum['content']}")
        processed_message = summary_message+recent_messages

        return processed_message
        

        
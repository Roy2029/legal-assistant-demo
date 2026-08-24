import json
import uuid
import threading
from pathlib import Path
from datetime import datetime
import hashlib


class SessionManager:

    def __init__(self):

        self.sessions_dir = Path("data/sessions")
        self.sessions_dir.mkdir(exist_ok=True)

        self.current_session = None
        self._lock = threading.Lock()
        
    
    def new_session(self):
        session_id = f"session_{hash_uuid(uuid.uuid4().hex[:8])}"
        session = {
            "session_id": session_id,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "name": f"新会话 {datetime.now().strftime('%m-%d %H:%M')}",
            "messages": []
            }
        self.current_session = session
        self.save(session_id)
        return session # 返回完整的会话对象 

    def save(self, session_id=None):
        if not session_id:
            if not self.current_session:
                self.new_session()
            session_id=self.current_session['session_id']

        if not self.current_session:
            return
        path = self.sessions_dir / f"{self.current_session['session_id']}.json"

        with self._lock:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.current_session, f, indent=2, ensure_ascii=False)

    def load(self, session_id=None):

        path = self.sessions_dir / f"{session_id}.json"

        if not path.exists():
            raise Exception("Session not found")

        with self._lock:
            with open(path, "r", encoding="utf-8") as f:
                self.current_session = json.load(f)

    def add_message(self, session_id, role, content):
        path = self.sessions_dir / f"{session_id}.json"
        if not path.exists():
            raise Exception("Session not found")
        with self._lock:
            with open(path, "r", encoding="utf-8") as f:
                session = json.load(f)
            session["messages"].append({
                    "role": role,
                    "content": content,
                    "time": datetime.now().strftime("%H:%M:%S")
                })
            with open(path, "w", encoding="utf-8") as f:
                json.dump(session, f, indent=2, ensure_ascii=False)

    def get_messages(self, session_id=None):
        if not session_id:
            if not self.current_session:
                return []
            sid = self.current_session['session_id']
        else:
            sid = session_id
        path = self.sessions_dir / f"{sid}.json"
        if not path.exists():
            return []
        with open(path, "r", encoding="utf-8") as f:
            session = json.load(f)
        return session.get("messages", [])
    
    # 添加获取完整会话信息的方法
    def get_session_info(self, session_id):
        path = self.sessions_dir / f"{session_id}.json"
        if not path.exists():
            return None
        with open(path, 'r', encoding='utf-8') as f:
            session = json.load(f)
        # 返回基本信息用于列表显示
        return {
            'session_id': session_id,
            'name': session.get('name', f"会话 {session_id[-8:]}") ,
            'created_at': session.get('created_at', ''),
            'message_count': len(session.get('messages', []))
        }

    # 修改list_sessions返回完整信息
    def list_sessions(self):
        sessions = []
        for file in self.sessions_dir.glob("*.json"):
            session_id = file.stem
            info = self.get_session_info(session_id)
            if info:
                sessions.append(info)
        # 按创建时间倒序排列
        return sorted(sessions, key=lambda x: x['created_at'], reverse=True)

    # 添加重命名方法
    def rename_session(self, session_id=None, new_name=None):
        if not session_id:
            if not self.current_session:
                return
            session_id = self.current_session['session_id']
        with self._lock:
            path = self.sessions_dir / f"{session_id}.json"
            if not path.exists():
                raise Exception("Session not found")
            with open(path, 'r', encoding='utf-8') as f:
                session = json.load(f)
            session['name'] = new_name
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(session, f, indent=2, ensure_ascii=False)

    def auto_title(self, session_id, first_message, llm):
        path = self.sessions_dir / f"{session_id}.json"
        if not path.exists():
            return
        with self._lock:
            with open(path, "r", encoding="utf-8") as f:
                session = json.load(f)
        # 如果已经有标题则不再生成
        if session.get("name") and not session["name"].startswith("新会话"):
            return

        prompt = f"""
    请根据用户的问题生成一个简短的会话标题。
    要求：
    - 不超过12个字
    - 不要标点
    - 直接输出标题

    用户问题：
    {first_message}
    """
        try:
            title = llm(prompt).strip()

            if len(title) > 20:
                title = title[:20]
            session["name"] = title
            with self._lock:
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(session, f, indent=2, ensure_ascii=False)

        except Exception:
            pass

    # ── Web 界面新增方法 ──────────────────────────────────────────

    def get_all_sessions(self) -> list[dict]:
        """返回所有会话摘要列表（与 list_sessions 同义，名称更清晰）。"""
        return self.list_sessions()

    def update_session(self, session_id: str, **kwargs) -> bool:
        """更新会话的指定字段。

        Args:
            session_id: 会话 ID
            **kwargs: 要更新的字段键值对

        Returns:
            是否更新成功
        """
        path = self.sessions_dir / f"{session_id}.json"
        if not path.exists():
            return False

        with self._lock:
            try:
                with open(path, "r", encoding="utf-8") as f:
                    session = json.load(f)
                session.update(kwargs)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(session, f, indent=2, ensure_ascii=False)
                return True
            except Exception:
                return False

    def delete_session(self, session_id: str) -> bool:
        """删除会话文件。

        Args:
            session_id: 会话 ID

        Returns:
            是否删除成功
        """
        path = self.sessions_dir / f"{session_id}.json"
        with self._lock:
            try:
                if path.exists():
                    path.unlink()
                    # 如果删除的是当前会话，清空 current_session
                    if self.current_session and self.current_session.get("session_id") == session_id:
                        self.current_session = None
                    return True
                return False
            except Exception:
                return False


def hash_uuid(uuid_str, length=8):
    """
    将UUID哈希映射到指定长度的Base62字符串
    
    Args:
        uuid_str: 原始UUID字符串
        length: 目标字符串长度
    
    Returns:
        指定长度的Base62字符串
    """
    # 使用SHA256哈希算法
    hash_object = hashlib.sha256(uuid_str.encode('utf-8'))
    hash_int = int.from_bytes(hash_object.digest(), byteorder='big')
    
    # Base62字符集
    base62_chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    result = ""
    
    # 转换为Base62
    while hash_int > 0 and len(result) < length:
        result = base62_chars[hash_int % 62] + result
        hash_int //= 62
    
    # 如果结果不够长，用第一个字符填充
    if len(result) < length:
        result = result.ljust(length, base62_chars[0])
    
    return result[:length]
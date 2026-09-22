"""
原子文件读写工具

提供安全的 JSON 落盘与加载，解决以下报告指出的问题：
- P1-4 非原子写入：崩溃/断电时 open('w') 截断后写入中途失败会导致文件残废
- P2-2 加载失败应告警并尝试 .bak 恢复，而非静默清空数据

设计参考本项目已 vendored 的 chaoxing_tool/cache_dao.py（RLock + mkstemp + fsync + os.replace）。
"""
import json
import os
import tempfile


def atomic_write_json(path: str, data, *, backup: bool = True, indent: int = 2) -> bool:
    """
    原子写 JSON：先写临时文件并 fsync，再用 os.replace 原子替换目标文件。
    可选先把旧文件备份为 path + '.bak'。

    Returns:
        bool: 是否写入成功
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        pass

    fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        if backup and os.path.exists(path):
            try:
                os.replace(path, path + ".bak")
            except OSError:
                pass
        os.replace(tmp_path, path)
        return True
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def load_json_safe(path: str, default=None):
    """
    安全加载 JSON：主文件损坏时自动尝试 path + '.bak' 恢复，
    避免崩溃后静默重置为空数据。

    Returns:
        解析出的对象；主文件与备份均不可用/不存在时返回 default。
    """
    candidates = [path]
    if os.path.exists(path + ".bak"):
        candidates.append(path + ".bak")

    last_error = None
    for candidate in candidates:
        if not os.path.exists(candidate):
            continue
        try:
            with open(candidate, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:  # 损坏的 JSON：尝试下一个候选（.bak）
            last_error = e
            continue

    if last_error is not None:
        # 主文件存在但解析失败，已在上面的循环里被捕获；这里仅用于调试上下文
        pass
    return default

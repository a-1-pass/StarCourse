# StarCourse CLI 模式镜像（无 GUI，不包含 PyQt6）
#
# 构建:
#   docker build -t starcourse-cli .
# 运行（交互式，挂载配置与数据目录以持久化）:
#   docker run --rm -it \
#     -v ${PWD}/engine/config.yml:/app/engine/config.yml \
#     -v ${PWD}/engine/data:/app/engine/data \
#     starcourse-cli
# Windows PowerShell 同理使用 ${PWD}；CMD 使用 %CD%。
FROM python:3.11-slim

# 基础运行环境：关闭字节码缓存、stdout 实时输出
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 安装依赖：CLI 形态排除 PyQt6（GUI 专用，减小镜像体积）
COPY requirements.txt ./
RUN grep -viE '^\s*(#|$)' requirements.txt | grep -vi '^PyQt6' > /tmp/requirements-cli.txt \
    && pip install --no-cache-dir -r /tmp/requirements-cli.txt

# 拷贝运行所需代码（.dockerignore 已排除 tests/docs/日志/数据库等）
COPY engine ./engine
COPY src ./src

# 数据目录（缓存/答案记录等），可挂载持久化
VOLUME ["/app/engine/data"]

ENTRYPOINT ["python", "engine/cli_entry.py"]

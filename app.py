"""Application entry point for the fire risk analysis system."""

import logging

from fire_safety.ui import launch_app

if __name__ == "__main__":
    # 每次分析会以 INFO 打印一行 [耗时] 明细。Gradio 与 uvicorn 都不配置 root
    # logger，缺省只输出 WARNING 以上，因此在这里显式打开 INFO（默认输出到
    # stderr，与 uvicorn 日志同流）。
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    launch_app()

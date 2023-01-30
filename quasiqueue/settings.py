from pydantic import BaseSettings


class Settings(BaseSettings):
    project_name: str = "quasiqueue"
    debug: bool = False
    num_processes: int = 2
    max_queue_size: int = 300
    prevent_requeuing_time: float = 300
    empty_queue_sleep_time: float = 1.00
    full_queue_sleep_time: float = 5.00
    queue_interaction_timeout: float = 0.01
    graceful_shutdown_timeout: float = 30
    lookup_block_size: int = 10
    max_jobs_per_process: int | None = 200

    class Config:
        env_prefix = "QUASIQUEUE_"


def get_named_settings(name: str) -> Settings:
    class QueueSettings(Settings):
        class Config:
            env_prefix = f"QUASIQUEUE_{name.upper()}_"

    return QueueSettings()

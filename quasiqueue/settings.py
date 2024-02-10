from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="QUASIQUEUE_")

    num_processes: int = Field(default=2, description="The number of reader processes to run.")
    max_queue_size: int = Field(default=300, description="The max allowed six of the queue.")
    prevent_requeuing_time: float = Field(
        default=300,
        description="The time in seconds that an item will be prevented from being readded to the queue.",
    )
    empty_queue_sleep_time: float = Field(
        default=1.00,
        description="The time in seconds that QuasiQueue will sleep the writer process when it returns no results.",
    )
    full_queue_sleep_time: float = Field(
        default=5.00,
        description="The time in seconds that QuasiQueue will sleep the writer process if the queue is completely full.",
    )
    queue_interaction_timeout: float = Field(
        default=0.01,
        description="The time QuasiQueue will wait for the Queue to be unlocked before throwing an error.",
    )
    graceful_shutdown_timeout: float = Field(
        default=30,
        description="The time in seconds that QuasiQueue will wait for readers to finish when it is asked to gracefully shutdown.",
    )
    lookup_block_size: int = Field(
        default=10,
        description="The default desired_items passed to the writer function. This will be adjusted lower depending on queue dynamics.",
    )
    max_jobs_per_process: int | None = Field(
        default=200,
        description="The number of jobs a reader process will run before it is replaced by a new process.",
    )


def get_named_settings(name: str) -> Settings:
    class QueueSettings(Settings):
        model_config = SettingsConfigDict(env_prefix=f"QUASIQUEUE_{name.upper()}_")

    return QueueSettings()

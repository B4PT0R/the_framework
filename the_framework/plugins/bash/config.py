from the_framework.agent import Config


class BashConfig(Config):
    default_cwd: str = "."
    inherit_interactive_environment: bool = True
    max_timeout_seconds: int = 900
    default_wait_seconds: int = 10
    max_wait_seconds: int = 60
    max_concurrent_processes: int = 4
    max_process_records: int = 32
    max_output_chars: int = 50_000

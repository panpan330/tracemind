from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRACEMIND_", env_file=".env.local", extra="ignore")

    control_db_url: str = "mysql+pymysql://tracemind_control_app:control_app_pwd@localhost:3306/tracemind_control"
    readonly_db_url: str = "mysql+pymysql://ai_investigator:investigator_pwd@localhost:3306/tracemind_business"
    order_service_url: str = "http://localhost:8081"
    inventory_service_url: str = "http://localhost:8082"
    demo_mode: bool = False
    demo_key: str = ""
    llm_mode: str = "fake"
    demo_approver_id: str = "demo-approver"
    checkpoint_path: str = "./data/checkpoints.sqlite"


settings = Settings()

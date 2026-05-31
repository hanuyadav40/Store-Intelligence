from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional


class Settings(BaseSettings):
    # Application
    APP_NAME: str = "Store Intelligence API"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # Detection model
    YOLO_MODEL: str = "yolov8n.pt"
    CONFIDENCE_THRESHOLD: float = 0.4
    IOU_THRESHOLD: float = 0.5

    # ByteTrack
    TRACK_ACTIVATION_THRESHOLD: float = 0.25
    LOST_TRACK_BUFFER: int = 30
    MINIMUM_MATCHING_THRESHOLD: float = 0.8
    FRAME_RATE: int = 25

    # Business logic
    SESSION_WINDOW_MINUTES: int = 30
    STAFF_DWELL_THRESHOLD_HOURS: float = 4.0
    GROUP_ENTRY_WINDOW_SECONDS: float = 2.0
    GROUP_ENTRY_MIN_SIZE: int = 2
    ENGAGEMENT_DWELL_SECONDS: float = 120.0  # 2 min = engaged visitor

    # Virtual entry line (fraction of frame height from top)
    ENTRY_LINE_Y_FRACTION: float = 0.4

    # Camera
    CAMERA_ID: str = "cam_01"

    # Data paths
    DATA_DIR: str = "/app/data"
    SALES_CSV_PATH: Optional[str] = "/app/data/sales.csv"

    # Prometheus
    ENABLE_PROMETHEUS: bool = True
    PROMETHEUS_PORT: int = 8001

    # Anomaly thresholds
    FOOTFALL_SPIKE_THRESHOLD: float = 2.0
    DWELL_TIME_ANOMALY_MINUTES: float = 60.0
    CROWD_THRESHOLD: int = 10

    # Demo / simulation
    DEMO_MODE: bool = True
    DEMO_INTERVAL_SECONDS: float = 5.0

    model_config = {"env_file": ".env", "case_sensitive": True}


settings = Settings()

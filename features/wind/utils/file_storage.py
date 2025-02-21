from datetime import datetime, timezone
from pathlib import Path
import logging
from features.common.services.model_run_service import ModelRun

logger = logging.getLogger(__name__)

class GFSFileStorage:
    """Handles storage and retrieval of GFS GRIB files."""
    
    def __init__(self, base_dir: str = "downloaded_data/gfs"):
        """Initialize the file storage with a base directory."""
        self.base_dir = Path(base_dir)
        self._ensure_storage_dir()
    
    def _ensure_storage_dir(self) -> None:
        """Ensure the storage directory exists."""
        self.base_dir.mkdir(parents=True, exist_ok=True)
    
    def get_file_path(self, station_id: str, model_run: ModelRun, forecast_hour: int) -> Path:
        """Generate the path for a GFS file."""
        date_str = model_run.run_date.strftime('%Y%m%d')
        cycle_str = f"{model_run.cycle_hour:02d}"
        return self.base_dir / f"gfs_wind_{station_id}_{date_str}_{cycle_str}z_f{forecast_hour:03d}.grib2"
    
    def is_file_valid(self, file_path: Path) -> bool:
        """Check if a file exists."""
        return file_path.exists()
    
    async def save_file(self, file_path: Path, content: bytes) -> bool:
        """Save file content to storage."""
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(file_path, 'wb') as f:
                f.write(content)
            return True
        except Exception as e:
            logger.error(f"Error saving file {file_path}: {str(e)}")
            return False
    
    def cleanup_old_files(self, current_run: ModelRun) -> None:
        """Delete files from older model runs."""
        try:
            current_pattern = f"*_{current_run.run_date.strftime('%Y%m%d')}_{current_run.cycle_hour:02d}z_*.grib2"
            deleted_count = 0
            
            for file_path in self.base_dir.glob("*.grib2"):
                # Keep files matching current model run pattern
                if current_pattern not in str(file_path):
                    file_path.unlink()
                    deleted_count += 1
                    
            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} files from previous model runs")
        except Exception as e:
            logger.error(f"Error during cleanup: {str(e)}") 
import aiohttp
import logging
import numpy as np
import xarray as xr
import cfgrib
import pandas as pd
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple
import asyncio
from fastapi import HTTPException

from features.wind.models.wind_types import WindForecastResponse, WindForecastPoint
from features.common.models.station_types import Station
from features.common.utils.conversions import UnitConversions
from features.wind.utils.file_storage import GFSFileStorage
from features.common.services.model_run_service import ModelRun

logger = logging.getLogger(__name__)

class GFSWindClient:
    """Client for fetching wind data from NOAA's GFS using NOMADS GRIB Filter."""
    
    BASE_URL = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"
    
    def __init__(self, model_run: Optional[ModelRun] = None):
        print(f"\n🔍 GFSWindClient initialized with model run: {model_run}")
        self.model_run = model_run
        self.file_storage = GFSFileStorage()
        
    def update_model_run(self, model_run: ModelRun):
        """Update the current model run and clean up old files."""
        print(f"\n🔄 Updating wind client model run to: {model_run}")
        self.model_run = model_run
        self.file_storage.cleanup_old_files(model_run)
        
    def _build_grib_filter_url(
        self,
        forecast_hour: int,
        lat: float,
        lon: float
    ) -> str:
        """Build URL for NOMADS GRIB filter service."""
        print(f"\n📊 Building URL for forecast hour {forecast_hour}")
        print(f"Current model run: {self.model_run}")
        print(f"Date: {self.model_run.run_date.strftime('%Y%m%d')}")
        print(f"Cycle: {self.model_run.cycle_hour:02d}")
        
        # Convert longitude to 0-360 range if needed
        if lon < 0:
            lon = 360 + lon
            
        # Create 1-degree bounding box
        lat_buffer = 0.15
        lon_buffer = 0.15
        
        params = {
            "dir": f"/gfs.{self.model_run.run_date.strftime('%Y%m%d')}/{self.model_run.cycle_hour:02d}/atmos",
            "file": f"gfs.t{self.model_run.cycle_hour:02d}z.pgrb2.0p25.f{forecast_hour:03d}",
            "var_UGRD": "on",
            "var_VGRD": "on",
            "var_GUST": "on",
            "lev_10_m_above_ground": "on",
            "lev_surface": "on",
            "subregion": "",
            "toplat": f"{lat + lat_buffer}",
            "bottomlat": f"{lat - lat_buffer}",
            "leftlon": f"{lon - lon_buffer}",
            "rightlon": f"{lon + lon_buffer}"
        }
        
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{self.BASE_URL}?{query}"
        print(f"🌐 Generated URL: {url}")
        return url
    
    async def _get_grib_file(
        self,
        url: str,
        station_id: str,
        forecast_hour: int
    ) -> Optional[Path]:
        """Get GRIB file from storage or download if needed."""
        file_path = self.file_storage.get_file_path(station_id, self.model_run, forecast_hour)
        
        # Check if we have a valid cached file
        if self.file_storage.is_file_valid(file_path):
            return file_path
            
        # Download if no valid file exists
        try:
            async with aiohttp.ClientSession() as session:
                logger.info(f"Downloading wind data for hour {forecast_hour:03d} from {url}")
                async with session.get(url, allow_redirects=True, timeout=300) as response:
                    if response.status == 404:
                        logger.warning(f"GRIB file not found for hour {forecast_hour:03d}: {url}")
                        return None
                    elif response.status != 200:
                        logger.error(f"Failed to download GRIB file: {response.status} - {url}")
                        return None
                        
                    content = await response.read()
                    if len(content) < 100:
                        logger.error(f"Downloaded file too small ({len(content)} bytes) for hour {forecast_hour:03d}")
                        return None
                        
                    if await self.file_storage.save_file(file_path, content):
                        logger.info(f"Successfully downloaded and saved wind data for hour {forecast_hour:03d}")
                        return file_path
                    return None
            
        except asyncio.TimeoutError:
            logger.error(f"Timeout downloading GRIB file for hour {forecast_hour:03d}: {url}")
            return None
        except Exception as e:
            logger.error(f"Error downloading GRIB file: {str(e)} - {url}")
            return None
    
    def _calculate_wind(self, u: float, v: float) -> tuple[float, float]:
        """Calculate wind speed and direction from U and V components."""
        try:
            speed = round((u * u + v * v) ** 0.5, 2)
            direction = round((270 - (180 / 3.14159) * (v > 0) * 3.14159 + (180 / 3.14159) * (v < 0) * 3.14159) % 360, 2)
            return speed, direction
        except Exception as e:
            logger.error(f"Error calculating wind: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Error calculating wind data: {str(e)}"
            )
            
    def _process_grib_data(
        self,
        grib_path: Path,
        lat: float,
        lon: float
    ) -> Optional[Tuple[datetime, float, float, float]]:
        """Process GRIB2 file and extract wind data for location."""
        try:
            ds = xr.open_dataset(
                grib_path,
                engine='cfgrib',
                decode_timedelta=False,
                backend_kwargs={'indexpath': ''}
            )
            
            try:
                # Get valid time from the dataset and ensure it's a datetime
                valid_time = pd.to_datetime(ds.valid_time.item()).to_pydatetime()
                if not isinstance(valid_time, datetime):
                    logger.error(f"Invalid time format from GRIB: {valid_time}")
                    return None
                
                # Ensure timezone is UTC
                if valid_time.tzinfo is None:
                    valid_time = valid_time.replace(tzinfo=timezone.utc)
                
                # Find nearest grid point
                lat_idx = abs(ds.latitude - lat).argmin().item()
                lon_idx = abs(ds.longitude - lon).argmin().item()
                
                # Extract values using known variable names - data is 2D (lat, lon)
                u = float(ds['u10'].values[lat_idx, lon_idx])
                v = float(ds['v10'].values[lat_idx, lon_idx])
                gust = float(ds['gust'].values[lat_idx, lon_idx])
                
                return valid_time, u, v, gust
            finally:
                ds.close()
                
        except Exception as e:
            logger.error(f"Error processing GRIB file: {str(e)}")
            return None
    
    async def get_station_wind_forecast(self, station: Station) -> WindForecastResponse:
        """Get 7-day wind forecast for a station."""
        try:
            print(f"\n🌪️ Getting wind forecast for station {station.station_id}")
            print(f"Model run state: {self.model_run}")
            
            if not self.model_run:
                logger.error("No model run available for wind forecast")
                raise HTTPException(
                    status_code=503,
                    detail="No model cycle currently available"
                )
                
            logger.info(f"Getting wind forecast for station {station.station_id} using cycle: {self.model_run.run_date.strftime('%Y%m%d')} {self.model_run.cycle_hour:02d}Z")
            
            # Get lat/lon from GeoJSON coordinates [lon, lat]
            lat = station.location.coordinates[1]
            lon = station.location.coordinates[0]
            
            forecasts: List[WindForecastPoint] = []
            total_hours = 0
            failed_hours = 0
            
            # Get forecasts at 3-hour intervals up to 168 hours (7 days)
            for hour in range(0, 169, 3):  # 0 to 168 inclusive
                try:
                    url = self._build_grib_filter_url(hour, lat, lon)
                    grib_path = await self._get_grib_file(url, station.station_id, hour)
                    
                    if not grib_path:
                        logger.warning(f"Missing forecast for hour {hour}")
                        failed_hours += 1
                        continue
                        
                    wind_data = self._process_grib_data(grib_path, lat, lon)
                    if wind_data:
                        valid_time, u, v, gust = wind_data
                        speed, direction = self._calculate_wind(u, v)
                        
                        forecasts.append(WindForecastPoint(
                            time=valid_time,
                            speed=UnitConversions.ms_to_mph(speed),
                            direction=direction,
                            gust=UnitConversions.ms_to_mph(gust)
                        ))
                        total_hours += 1
                except Exception as e:
                    logger.error(f"Error processing hour {hour}: {str(e)}")
                    failed_hours += 1
                    continue
            
            if not forecasts:
                logger.error(f"No forecast data available. Failed to process {failed_hours} out of {total_hours + failed_hours} hours.")
                raise HTTPException(
                    status_code=503,
                    detail=f"No forecast data available. Failed to process {failed_hours} forecast hours."
                )
                
            # Sort forecasts by time
            forecasts.sort(key=lambda x: x.time)
            logger.info(f"Generated forecast with {total_hours} time points (failed: {failed_hours})")
            
            return WindForecastResponse(
                station=station,
                model_run=f"{self.model_run.run_date.strftime('%Y%m%d')}_{self.model_run.cycle_hour:02d}Z",
                forecasts=forecasts
            )
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting wind forecast for station {station.station_id}: {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Error processing wind forecast: {str(e)}"
            ) 
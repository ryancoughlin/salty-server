import logging
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional
import json
import xarray as xr
import pandas as pd
import asyncio

from core.config import settings
from utils.model_time import get_latest_model_run

logger = logging.getLogger(__name__)

class WaveDataProcessor:
    _cached_dataset = None
    _cached_model_run = None
    
    def __init__(self, data_dir: str = settings.data_dir):
        self.data_dir = Path(data_dir)
        
    def get_current_model_run(self) -> tuple[str, str]:
        """Get latest available model run."""
        return get_latest_model_run()

    async def preload_dataset(self) -> None:
        """Preload the dataset for the current model run."""
        try:
            model_run, date = self.get_current_model_run()
            logger.info(f"Preloading dataset for model run {date} {model_run}z")
            start_time = datetime.now()
            
            # Load dataset in executor to not block event loop
            await asyncio.get_event_loop().run_in_executor(
                None,
                self._load_forecast_dataset,
                model_run,
                date
            )
            
            duration = (datetime.now() - start_time).total_seconds()
            logger.info(f"Completed dataset preload in {duration:.2f}s")
        except Exception as e:
            logger.error(f"Error preloading dataset: {str(e)}")
            raise

    def _load_forecast_dataset(self, model_run: str, date: str) -> Optional[xr.Dataset]:
        if self._cached_dataset is not None and self._cached_model_run == model_run:
            logger.debug("Using cached dataset")
            return self._cached_dataset
            
        logger.info(f"Loading forecast files for model run {date} {model_run}z...")
        start_time = datetime.now()
        
        try:
            # Get current model run time
            model_run_time = datetime.strptime(f"{date} {model_run}00", "%Y%m%d %H%M")
            model_run_time = model_run_time.replace(tzinfo=timezone.utc)
            
            # For complete coverage of today, we need:
            # - Current run for most hours
            # - Previous run for early hours
            needed_runs = []
            
            # Current run
            if (datetime.now(timezone.utc) - model_run_time) >= timedelta(hours=3, minutes=30):
                needed_runs.append((date, model_run))
                logger.info(f"Using current run {date} {model_run}z")
            
            # Previous run (for early hours)
            prev_run = str(int(model_run) - 6).zfill(2)  # Simple 6-hour lookback
            if prev_run not in settings.model_runs:
                # If previous run would be yesterday, get last run of yesterday
                prev_date = (model_run_time - timedelta(days=1)).strftime("%Y%m%d")
                prev_run = "18"  # Last run of the day
            else:
                prev_date = date
                
            prev_run_time = datetime.strptime(f"{prev_date} {prev_run}00", "%Y%m%d %H%M")
            prev_run_time = prev_run_time.replace(tzinfo=timezone.utc)
            
            if (datetime.now(timezone.utc) - prev_run_time) >= timedelta(hours=3, minutes=30):
                needed_runs.append((prev_date, prev_run))
                logger.info(f"Using previous run {prev_date} {prev_run}z for early hours")
            
            if not needed_runs:
                logger.warning("No model runs available")
                return None
            
            # Load datasets
            all_datasets = []
            base_url = settings.base_url
            
            for run_date, run_hour in needed_runs:
                forecast_files = []
                max_hours = 24 if run_date != date or run_hour != model_run else 120
                
                # Only load files we need
                for hour in [h for h in settings.forecast_hours if h <= max_hours]:
                    filename = f"gfswave.t{run_hour}z.{settings.models['atlantic']['name']}.f{str(hour).zfill(3)}.grib2"
                    file_path = self.data_dir / filename
                    
                    if file_path.exists():
                        forecast_files.append((hour, file_path))
                        logger.debug(f"Found file: {filename}")
                
                if not forecast_files:
                    logger.warning(f"No files found for run {run_date} {run_hour}z")
                    continue
                
                logger.info(f"Loading {len(forecast_files)} files from run {run_date} {run_hour}z")
                
                # Process files for this run
                run_time = datetime.strptime(f"{run_date} {run_hour}00", "%Y%m%d %H%M")
                run_time = run_time.replace(tzinfo=timezone.utc)
                
                for hour, file_path in forecast_files:
                    try:
                        ds = xr.open_dataset(file_path, engine='cfgrib', backend_kwargs={
                            'time_dims': ('time',),
                            'indexpath': '',
                            'filter_by_keys': {'typeOfLevel': 'surface'}
                        })
                        forecast_time = run_time + timedelta(hours=hour)
                        ds = ds.assign_coords(time=forecast_time)
                        all_datasets.append((forecast_time, ds))
                    except Exception as e:
                        logger.error(f"Error loading {file_path}: {str(e)}")
                        if ds is not None:
                            ds.close()
            
            if not all_datasets:
                logger.warning("No forecast data could be loaded")
                return None
            
            try:
                # Keep most recent forecast for each timestamp
                all_datasets.sort(key=lambda x: x[0])
                unique_datasets = {}
                for time, ds in all_datasets:
                    if time not in unique_datasets:
                        unique_datasets[time] = ds
                
                # Combine and sort
                combined = xr.concat([ds for _, ds in sorted(unique_datasets.items())], 
                                   dim="time", 
                                   combine_attrs="override")
                combined = combined.sortby('time')
                
                logger.info(f"Processed {len(unique_datasets)} forecasts in {(datetime.now() - start_time).total_seconds():.1f}s")
                logger.info(f"Time range: {combined.time.values[0]} to {combined.time.values[-1]}")
                
                WaveDataProcessor._cached_dataset = combined
                WaveDataProcessor._cached_model_run = model_run
                return combined
                
            finally:
                for _, ds in all_datasets:
                    ds.close()
                    
        except Exception as e:
            logger.error(f"Error loading forecast dataset: {str(e)}")
            if self._cached_dataset is not None:
                logger.info("Using cached dataset")
                return self._cached_dataset
            return None

    def process_station_forecast(self, station_id: str) -> Dict:
        """Process wave model forecast for a station."""
        try:
            # Get station metadata
            station = self._get_station_metadata(station_id)
            if not station:
                raise ValueError(f"Station {station_id} not found")
            
            # Get current model run info
            model_run, date = self.get_current_model_run()
            
            # Load or get cached dataset
            full_forecast = self._load_forecast_dataset(model_run, date)
            if full_forecast is None:
                logger.warning(f"No forecast data available for station {station_id}")
                return {
                    "station_id": station_id,
                    "name": station["name"],
                    "location": station["location"],
                    "model_run": f"{date} {model_run}z",
                    "forecasts": [],
                    "metadata": station,
                    "status": "no_data"
                }

            # Convert all times to EST and sort them
            est_times = []
            for time in full_forecast.time.values:
                utc_time = pd.Timestamp(time).tz_localize('UTC')
                est_time = utc_time.tz_convert('EST')
                est_times.append(est_time)
            
            est_times.sort()
            
            # Find nearest grid point (convert longitude to 0-360)
            lat = station["location"]["coordinates"][1]
            lon = station["location"]["coordinates"][0]
            if lon < 0:
                lon = lon + 360
            
            lat_idx = abs(full_forecast.latitude - lat).argmin().item()
            lon_idx = abs(full_forecast.longitude - lon).argmin().item()
            
            # Simple 1:1 variable mapping with unit conversions
            variables = {
                # Wind (m/s to mph)
                'ws': ('wind_speed', lambda x: x * 2.237),
                'wdir': ('wind_direction', lambda x: x),  # degrees, no conversion
                # Wave heights (m to ft)
                'swh': ('wave_height', lambda x: x * 3.28084),
                'shww': ('wind_wave_height', lambda x: x * 3.28084),
                'shts': ('swell_height', lambda x: x * 3.28084),
                # Periods (seconds, no conversion needed)
                'perpw': ('wave_period', lambda x: x),
                'mpww': ('wind_wave_period', lambda x: x),
                'mpts': ('swell_period', lambda x: x),
                # Directions (degrees, no conversion needed)
                'dirpw': ('wave_direction', lambda x: x),
                'wvdir': ('wind_wave_direction', lambda x: x),
                'swdir': ('swell_direction', lambda x: x)
            }
            
            # Extract point data for all variables
            point_data = {}
            for var, (output_name, convert) in variables.items():
                if var in full_forecast:
                    # Extract and convert units
                    data = full_forecast[var].isel(
                        latitude=lat_idx, 
                        longitude=lon_idx
                    ).values
                    point_data[output_name] = convert(data)
            
            # Process each timestamp
            forecasts = []
            for i, time in enumerate(full_forecast.time.values):
                # First localize naive timestamp to UTC, then convert to EST
                utc_time = pd.Timestamp(time).tz_localize('UTC')
                forecast_time = utc_time.tz_convert('EST')
                forecast = {"time": forecast_time.isoformat()}
                
                # Group variables by type
                wind = {}
                wave = {}
                
                # Add wind data
                if 'wind_speed' in point_data and 'wind_direction' in point_data:
                    speed = float(point_data['wind_speed'][i])
                    direction = float(point_data['wind_direction'][i])
                    if not pd.isna(speed) and not pd.isna(direction):
                        wind = {
                            'speed': round(speed, 1),
                            'direction': round(direction, 1)
                        }

                # Process primary wave parameters first
                primary_wave_vars = {
                    'wave_height': 'height',
                    'wave_period': 'period',
                    'wave_direction': 'direction'
                }
                
                # Add primary wave parameters
                for src, dest in primary_wave_vars.items():
                    if src in point_data:
                        val = float(point_data[src][i])
                        if not pd.isna(val):
                            wave[dest] = round(val, 1)
                
                # Add wind wave components if available
                wind_wave_vars = {
                    'wind_wave_height': 'wind_height',
                    'wind_wave_period': 'wind_period',
                    'wind_wave_direction': 'wind_direction'
                }
                
                # Only add wind wave components if all are available
                if all(src in point_data for src in wind_wave_vars.keys()):
                    wind_wave_data = {}
                    for src, dest in wind_wave_vars.items():
                        val = float(point_data[src][i])
                        if not pd.isna(val):
                            wind_wave_data[dest] = round(val, 1)
                    
                    # Only add wind wave components if we have all the data
                    if len(wind_wave_data) == len(wind_wave_vars):
                        wave.update(wind_wave_data)
                
                # Add swell components
                swell = []
                if all(var in point_data for var in ['swell_height', 'swell_period', 'swell_direction']):
                    for j in range(3):  # 3 swell components
                        height = float(point_data['swell_height'][i, j])
                        period = float(point_data['swell_period'][i, j])
                        direction = float(point_data['swell_direction'][i, j])
                        
                        if not any(pd.isna(x) for x in [height, period, direction]):
                            swell.append({
                                'height': round(height, 1),
                                'period': round(period, 1),
                                'direction': round(direction, 1)
                            })

                forecast.update({
                    'wind': wind,
                    'wave': wave,
                    'swell': swell
                })
                
                forecasts.append(forecast)
            
            # Sort forecasts by time
            forecasts.sort(key=lambda x: x['time'])
            
            return {
                "station_id": station_id,
                "name": station["name"],
                "location": station["location"],
                "model_run": f"{date} {model_run}z",
                "forecasts": forecasts,
                "metadata": station
            }
            
        except Exception as e:
            logger.error(f"Error processing forecast for station {station_id}: {str(e)}")
            raise
            
    def _get_station_metadata(self, station_id: str) -> Optional[Dict]:
        """Get station metadata from JSON file."""
        try:
            with open("ndbcStations.json") as f:
                stations = json.load(f)
                return next((s for s in stations if s["id"] == station_id), None)
        except Exception as e:
            logger.error(f"Error loading station metadata: {str(e)}")
            raise 
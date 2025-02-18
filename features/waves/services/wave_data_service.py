import logging
from typing import Dict, List
from fastapi import HTTPException
from datetime import datetime

from features.stations.models.summary_types import StationSummary
from features.waves.models.wave_types import (
    WaveData,
    WaveForecastPoint,
    WaveForecastResponse
)
from core.cache import cached
from features.waves.services.noaa_gfs_client import NOAAGFSClient
from features.waves.services.ndbc_buoy_client import NDBCBuoyClient
from features.weather.services.summary_service import WeatherSummaryService
from features.stations.services.station_service import StationService

logger = logging.getLogger(__name__)

class WaveDataService:
    def __init__(
        self, 
        gfs_client: NOAAGFSClient,
        weather_service: WeatherSummaryService, 
        buoy_client: NDBCBuoyClient,
        station_service: StationService
    ):
        self.gfs_client = gfs_client
        self.weather_service = weather_service
        self.buoy_client = buoy_client
        self.station_service = station_service

    @cached(namespace="wave_forecast")
    async def get_station_forecast(self, station_id: str) -> WaveForecastResponse:
        """Get wave model forecast for a specific station."""
        try:
            station = self.station_service.get_station(station_id)
            if not station:
                raise HTTPException(
                    status_code=404,
                    detail=f"Station {station_id} not found"
                )
                
            # Get forecast directly from GFS wave service
            gfs_forecast = await self.gfs_client.get_station_forecast(station_id, station)
            
            if not gfs_forecast:
                logger.error(f"No forecast data available for station {station_id}")
                raise HTTPException(
                    status_code=503,
                    detail="Forecast data not available. Please try again later."
                )
            
            # Convert to API response format
            forecast_points = []
            for point in gfs_forecast.forecasts:
                # Get primary wave component (highest)
                primary_wave = point.waves[0] if point.waves else None
                forecast_points.append(WaveForecastPoint(
                    time=point.timestamp,
                    wave=WaveData(
                        height=primary_wave.height_ft if primary_wave else None,
                        period=primary_wave.period if primary_wave else None,
                        direction=primary_wave.direction if primary_wave else None
                    )
                ))
            
            return WaveForecastResponse(
                station=station,
                forecasts=forecast_points,
                model_run=f"{gfs_forecast.cycle.date} {gfs_forecast.cycle.hour}z"
            )
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error getting forecast for station {station_id}: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

    @cached(namespace="wave_summary")
    async def get_station_wave_summary(self, station_id: str) -> StationSummary:
        """Get a wave conditions summary for a specific station."""
        try:
            # Get fresh forecast
            forecast = await self.get_station_forecast(station_id)
            if not forecast or not forecast.forecasts:
                raise HTTPException(status_code=404, detail="No forecast available for station")

            # Convert GFS forecast points to format expected by summary service
            forecast_points = [
                {
                    "time": point.time,
                    "wave": {
                        "height": point.wave.height if point.wave else None,
                        "period": point.wave.period if point.wave else None,
                        "direction": point.wave.direction if point.wave else None
                    }
                }
                for point in forecast.forecasts
            ]

            # Generate fresh summary
            conditions = self.weather_service.generate_summary(forecast_points)
            
            # Create metadata from forecast
            metadata = {
                "id": forecast.station.station_id,
                "name": forecast.station.name,
                "location": forecast.station.location.dict(),
                "type": forecast.station.type
            }
            
            return StationSummary(
                station_id=station_id,
                metadata=metadata,
                summary=conditions,
                last_updated=datetime.now()
            )
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error generating summary for station {station_id}: {str(e)}")
            raise HTTPException(status_code=500, detail=str(e))

    @cached(namespace="wave_stations_geojson")
    async def get_stations_geojson(self) -> Dict:
        """Get all wave monitoring stations in GeoJSON format."""
        try:
            stations = self.station_service.get_stations()
            
            features = []
            for station in stations:
                feature = {
                    "type": "Feature",
                    "geometry": station.location.dict(),
                    "properties": {
                        "id": station.station_id,
                        "name": station.name,
                    }
                }
                features.append(feature)
            
            return {
                "type": "FeatureCollection",
                "features": features
            }
            
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=f"Error converting stations to GeoJSON: {str(e)}"
            ) 
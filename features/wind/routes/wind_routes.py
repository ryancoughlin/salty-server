from fastapi import APIRouter, Depends, Request
from features.wind.models.wind_types import WindForecastResponse
from features.wind.services.wind_service import WindService
from core.cache import cached
from typing import Optional

router = APIRouter(
    prefix="/wind",
    tags=["Wind"],
    responses={
        404: {"description": "Station not found"},
        503: {"description": "Forecast service unavailable"}
    }
)

def get_wind_service(request: Request) -> WindService:
    """Get WindService instance from app state."""
    return request.app.state.wind_service

def wind_cache_key_builder(
    func,
    namespace: Optional[str] = "",
    *args,
    **kwargs,
) -> str:
    """Build a cache key that includes the station ID."""
    station_id = kwargs.get("station_id", "")
    return f"{namespace}:{station_id}"

@router.get(
    "/{station_id}/forecast",
    response_model=WindForecastResponse,
    summary="Get wind forecast for a station",
    description="Returns a 7-day wind forecast at 3-hour intervals from GFS for the specified station"
)
@cached(
    namespace="wind_forecast",
    expire=14400,  # 4 hours (max time between model runs)
    key_builder=wind_cache_key_builder
)
async def get_station_wind_forecast(
    station_id: str,
    wind_service: WindService = Depends(get_wind_service)
) -> WindForecastResponse:
    """Get wind forecast for a specific station."""
    return await wind_service.get_station_wind_forecast(station_id) 
// services/stationService.js
const connectDB = require('../database')
const Station = require('../models/station.model')

const fetchClosestStation = async (lat, lon) => {
  const db = await connectDB()
  const query = {
    location: {
      $near: {
        $geometry: {
          type: 'Point',
          coordinates: [parseFloat(lon), parseFloat(lat)]
        }
      }
    }
  }

  return await Station.findOne(query)
}

const fetchAllStations = async () => {
  const db = await connectDB()
  const stations = await Station.find({})
  return stations
}

module.exports = { fetchClosestStation, fetchAllStations }

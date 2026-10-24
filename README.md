# Forecast-Accuracy-Dashboard

## Problem Statement

Problem Statement: Accessing specific 1-day-ahead historical forecasts (what was predicted exactly 24 hours prior) is difficult because most services purge short-term forecast data shortly after it expires. But this data is valuable for the recent rise in weather betting in prediction markets. This project aims to collect, store, and analyze 1-day-ahead historical forecasts from various sources for various locations, making it easier to analyze and utilize this data for research and betting assessment purposes.

By collecting the 1-day-ahead historical forecasts from various weather services, we can create a comprehensive dataset that can be used for analysis and research. This dataset will allow users to compare the accuracy of different weather services, identify trends in forecasting, and make informed decisions in weather betting markets.

## Data Sources

popular weather services listed below provide APIs that allow developers to access weather data programmatically. By leveraging these APIs, we can automate the process of collecting 1-day-ahead historical forecasts for various locations.

weather services to consider for data collection include:
- OpenWeatherMap
- Weatherbit
- AccuWeather
- WeatherAPI
- google weather
- Ambee Weather API
- MeteoMatics
- tomorrow.io

if any other weather services dont provide APIs for accessing historical forecasts, we can explore alternative methods such as web scraping or partnering with the service providers to obtain the necessary data.

Daily high/low temperature markets settle based on the final climate report issued by the National Weather Service (NWS), thus, the forecasts collected from other services can be compared against the NWS data to evaluate their accuracy. By analyzing the discrepancies between the forecasts and the actual weather conditions reported by the NWS, we can assess the reliability of different weather services and provide valuable insights for users in weather betting markets.

## Data Collection

you cant just look at general weather data for a city, as the weather can vary significantly within different areas of the same city. Therefore, it is important to collect forecasts from specific locations within a city to ensure accuracy and relevance for users in weather betting markets.

High and low temperature contracts for ____ resolve using data from The Weather Company (weather.com/kalshi) recorded at the ___ Central Park ___ station: 
- New York City, NY (Central Park)
- Los Angeles, CA (Los Angeles International Airport)
- Chicago, IL (O'Hare International Airport)
- Miami, FL (Miami International Airport)
- Dallas, TX (Dallas Love Field Airport)
- Seattle, WA (Seattle-Tacoma International Airport)
- Boston, MA (Boston Logan International Airport)
- San Francisco, CA (San Francisco International Airport)

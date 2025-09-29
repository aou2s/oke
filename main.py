import discord
from discord import app_commands
import requests
from datetime import datetime, timedelta
import os
import time
from flask import Flask
from threading import Thread

# Load tokens from environment variables
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
TFL_APP_KEY = os.getenv("TFL_APP_KEY")

# Validate that tokens are set
if not DISCORD_TOKEN:
    raise ValueError("DISCORD_TOKEN environment variable is not set")
if not TFL_APP_KEY:
    raise ValueError("TFL_APP_KEY environment variable is not set")

# Create Flask app for keep-alive
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is alive!", 200

@app.route('/health')
def health():
    uptime_seconds = int(time.time() - start_time) if start_time else 0
    return {
        "status": "online",
        "uptime_seconds": uptime_seconds
    }, 200

def run_flask():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    thread = Thread(target=run_flask)
    thread.daemon = True
    thread.start()

# Set up the bot with necessary intents
intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

# Track when the bot started
start_time = None

@client.event
async def on_ready():
    """Event handler for when the bot is ready."""
    global start_time
    start_time = time.time()
    await tree.sync()
    if client.user:
        print(f'Logged in as {client.user} (ID: {client.user.id})')
        print('------')

@tree.command(name="ping", description="Check the bot's latency and uptime")
async def ping(interaction: discord.Interaction):
    """
    Slash command to check bot latency and uptime.
    """
    # Calculate latency
    latency = round(client.latency * 1000)
    
    # Calculate uptime
    if start_time:
        uptime_seconds = int(time.time() - start_time)
        uptime_delta = timedelta(seconds=uptime_seconds)
        
        # Format uptime nicely
        days = uptime_delta.days
        hours, remainder = divmod(uptime_delta.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        
        if days > 0:
            uptime_str = f"{days}d {hours}h {minutes}m {seconds}s"
        elif hours > 0:
            uptime_str = f"{hours}h {minutes}m {seconds}s"
        elif minutes > 0:
            uptime_str = f"{minutes}m {seconds}s"
        else:
            uptime_str = f"{seconds}s"
    else:
        uptime_str = "Unknown"
    
    embed = discord.Embed(
        title="🏓 Pong!",
        color=0xffb7c5
    )
    embed.add_field(name="Latency", value=f"{latency}ms", inline=True)
    embed.add_field(name="Uptime", value=uptime_str, inline=True)
    
    await interaction.response.send_message(embed=embed)

@tree.command(name="route", description="Get bus registration plates for a specific TFL route.")
async def route(interaction: discord.Interaction, route_number: str):
    """
    Slash command to fetch and display bus registration plates for a given route.
    Args:
        interaction: The interaction object from Discord.
        route_number: The bus route number to query.
    """
    await interaction.response.defer()

    # Construct the TFL API URL - using Arrivals endpoint to get vehicle data
    url = f"https://api.tfl.gov.uk/Line/{route_number}/Arrivals"
    params = {'app_key': TFL_APP_KEY}

    response = None
    try:
        # Make the request to the TFL API
        response = requests.get(url, params=params)
        response.raise_for_status()  # Raise an exception for bad status codes

        data = response.json()

        if not data:
            await interaction.followup.send(f"No active buses found for route **{route_number}**. It might be an invalid route or there's no service at the moment.")
            return

        # Extract vehicle registration plates and destinations from arrivals data
        bus_info = {}
        for arrival in data:
            vehicle_id = arrival.get('vehicleId')
            destination = arrival.get('destinationName', 'Unknown Destination')
            station_name = arrival.get('stationName', 'Unknown Stop')
            expected_arrival = arrival.get('expectedArrival', 'N/A')

            # Convert ISO timestamp to readable time if available
            time_due = "N/A"
            if expected_arrival != 'N/A':
                try:
                    dt = datetime.fromisoformat(expected_arrival.replace('Z', '+00:00'))
                    time_due = dt.strftime('%H:%M')
                except Exception:
                    time_due = "N/A"

            if vehicle_id and vehicle_id != 'N/A':
                # Store the first arrival info for each vehicle
                if vehicle_id not in bus_info:
                    bus_info[vehicle_id] = {
                        'destination': destination,
                        'next_stop': station_name,
                        'time_due': time_due
                    }

        # Fetch fleet codes from bustimes.org API for each vehicle
        bus_data = []
        for reg, info in bus_info.items():
            fleet_code = "N/A"
            try:
                bt_url = "https://bustimes.org/api/vehicles/"
                bt_params = {'reg': reg.upper().replace(" ", "")}
                bt_response = requests.get(bt_url, params=bt_params, timeout=3)
                bt_response.raise_for_status()
                bt_data = bt_response.json()

                if bt_data.get('results'):
                    vehicle = bt_data['results'][0]
                    fleet_code = vehicle.get('fleet_code') or vehicle.get('fleet_number') or "N/A"
            except Exception as e:
                print(f"Error fetching fleet code for {reg}: {e}")

            bus_data.append((reg, info['destination'], fleet_code, info['next_stop'], info['time_due']))

        # Sort by registration
        sorted_buses = sorted(bus_data)

        if not sorted_buses:
            await interaction.followup.send(f"Could not find any active buses for route **{route_number}**. The route may not exist or there may be no active service.")
            return

        # Format the response as an embed with single column
        embed = discord.Embed(
            title=f"<:Buses:1384531695369191505> Active buses on route {route_number}",
            color=0xffb7c5
        )

        # Create single column format: Fleet Code - Registration towards Destination due Time at Stop
        bus_lines = []
        for reg, dest, fleet, stop, time in sorted_buses:
            line = f"{fleet} - {reg} towards {dest} due {time} at {stop}"
            bus_lines.append(line)

        # Join all lines with newlines for single column display
        bus_info_text = "\n".join(bus_lines)

        # Add as a single field with no inline (takes full width)
        embed.add_field(name="Vehicle Info", value=bus_info_text, inline=False)

        await interaction.followup.send(embed=embed)

    except requests.exceptions.HTTPError as http_err:
        if response and response.status_code == 404:
            await interaction.followup.send(f"Route **{route_number}** could not be found. Please check the route number and try again.")
        else:
            await interaction.followup.send(f"An HTTP error occurred: {http_err}")
    except Exception as e:
        print(f"An error occurred: {e}")
        await interaction.followup.send("Sorry, an unexpected error occurred while fetching the bus data.")

async def vehicle_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """
    Autocomplete function for vehicle registration search.
    Args:
        interaction: The interaction object from Discord.
        current: The current text the user has typed.
    Returns:
        A list of up to 25 vehicle registration choices.
    """
    if len(current) < 2:
        return []

    try:
        # Query the bustimes.org API with the current input
        url = "https://bustimes.org/api/vehicles/"
        params = {'search': current.upper().replace(" ", "")}

        response = requests.get(url, params=params, timeout=5)
        response.raise_for_status()

        data = response.json()

        # Create choices from the results (Discord limits to 25 choices)
        choices = []
        for vehicle in data.get('results', [])[:25]:
            reg = vehicle.get('reg', '')
            operator = vehicle.get('operator', {}).get('name', 'Unknown Operator')
            fleet_num = vehicle.get('fleet_number', '')

            # Create a display name with operator info
            if fleet_num:
                display_name = f"{reg} - {operator} (Fleet: {fleet_num})"
            else:
                display_name = f"{reg} - {operator}"

            # Truncate if too long (Discord limit is 100 characters)
            if len(display_name) > 100:
                display_name = display_name[:97] + "..."

            choices.append(app_commands.Choice(name=display_name, value=reg))

        return choices

    except Exception as e:
        print(f"Autocomplete error: {e}")
        return []

@tree.command(name="vehicle", description="Get detailed information about a specific vehicle from bustimes.org")
@app_commands.autocomplete(registration=vehicle_autocomplete)
async def vehicle(interaction: discord.Interaction, registration: str):
    """
    Slash command to fetch and display vehicle information from bustimes.org API.
    Args:
        interaction: The interaction object from Discord.
        registration: The vehicle registration plate (e.g., BF63HDG)
    """
    await interaction.response.defer()

    # Construct the bustimes.org API URL
    url = f"https://bustimes.org/api/vehicles/"
    params = {'reg': registration.upper().replace(" ", "")}

    response = None
    try:
        # Make the request to the bustimes.org API
        response = requests.get(url, params=params)
        response.raise_for_status()

        data = response.json()

        # Check if we got results
        if not data.get('results'):
            await interaction.followup.send(f"No vehicle found with registration **{registration}**.")
            return

        # Get the first result (should be the matching vehicle)
        vehicle_data = data['results'][0]

        # Create an embed with vehicle information
        embed = discord.Embed(
            title=f"Vehicle Information - {vehicle_data.get('reg', 'Unknown')}",
            color=0xffb7c5
        )

        # Add available fields (inline=False makes them stack vertically)
        if vehicle_data.get('operator'):
            embed.add_field(name="**Operator**", value=vehicle_data['operator'].get('name', 'Unknown'), inline=False)

        if vehicle_data.get('fleet_number'):
            embed.add_field(name="**Fleet Number**", value=vehicle_data['fleet_number'], inline=False)

        if vehicle_data.get('fleet_code'):
            embed.add_field(name="**Fleet Code**", value=vehicle_data['fleet_code'], inline=False)

        if vehicle_data.get('vehicle_type'):
            embed.add_field(name="**Vehicle Type**", value=vehicle_data['vehicle_type'].get('name', 'Unknown'), inline=False)

        if vehicle_data.get('livery'):
            embed.add_field(name="**Livery**", value=vehicle_data['livery'].get('name', 'Unknown'), inline=False)

        if vehicle_data.get('chassis'):
            embed.add_field(name="**Chassis**", value=vehicle_data['chassis'], inline=False)

        if vehicle_data.get('name'):
            embed.add_field(name="**Name**", value=vehicle_data['name'], inline=False)

        if vehicle_data.get('notes'):
            embed.add_field(name="**Notes**", value=vehicle_data['notes'], inline=False)

        # Add URL to bustimes.org page
        if vehicle_data.get('url'):
            embed.add_field(name="**More Info**", value=f"[View on bustimes.org]({vehicle_data['url']})", inline=False)

        await interaction.followup.send(embed=embed)

    except requests.exceptions.HTTPError as http_err:
        if response and response.status_code == 404:
            await interaction.followup.send(f"Vehicle **{registration}** could not be found.")
        else:
            await interaction.followup.send(f"An HTTP error occurred: {http_err}")
    except Exception as e:
        print(f"An error occurred: {e}")
        await interaction.followup.send("Sorry, an unexpected error occurred while fetching the vehicle data.")

# Start the keep-alive server
keep_alive()

# Run the bot
client.run(DISCORD_TOKEN)

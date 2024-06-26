# About

This plugin extends your bot with the feature to automatically kick clients from the TeamSpeak server, when they are idle since a longer time and you need more free slots for other potential joining clients.


# Available Commands

The following table shows all available arguments for the command `!kickinactiveclients` of this plugin:

| Argument | Description |
| ---:   | :--- |
| `version` | Sends a text message with the version of this plugin. |
| `start` | Start this plugin |
| `stop` | Stop this plugin |
| `restart` | Restarts this plugin |


# Configuration

Enable this plugin by adding the following line under the `Plugins` section to your `config.ini`:

```
[Plugins]
KickInactiveClients: kick_inactive_clients.main
```

## Options

This plugin supports the following options:

| Option | Default | Description |
| ---: | :---: | :--- |
| `auto_start` | `True` | Either if the plugin should automatically start when the Bot starts and it's configured or not. |
| `enable_dry_run` | `False` | Set to `True`, if you want to test the plugin without executing the actual tasks. Instead it logs what it would have done. |
| `frequency` | `300.0` | The frequency in seconds how often (and fast) the plugin should react (e.g. somebody is idle, every 300 seconds the bot would notice this and do something). |
| `exclude_servergroups` | `None` | Provide a comma seperated list of servergroup names, which should never get kicked by the bot. |
| `min_idle_time_seconds` | `7200` | The minimum time in seconds a client must be idle to get kicked from the server. |
| `min_clientsonline_kick_threshold` | `108` | Only start kicking idle clients from the server, when more clients than this value are online. Set this to `0` to always kick idle clients. |
| `kick_reason_message` | `Sorry for kicking, but we need slots!` | The kick reason message, which will be shown the respective kicked client. (Maximum supported length: 40 characters) |

If you need to change some of these default options, simply add them to your `config.ini` under the respective `ModuleName` section:

```
[kick_inactive_clients]
frequency: 60.0
exclude_servergroups: Server Admin,Bot
min_idle_time_seconds: 3600
min_clientsonline_kick_threshold: 55
```

Please keep in mind, that you need to reload the plugin afterwards. Either by restarting the entire bot or by using a plugin command, if it has one.


# Required Permissions

This plugin requires the following permissions on your TeamSpeak server:

| Permission | Explanation |
| ---: | :--- |
| `b_virtualserver_info_view` | Allow the bot to get the server information. Required for e.g. getting the current amount of used slots of the virtual server. |
| `b_virtualserver_client_list` | Allow the bot to get a list of all connected clients on your virtual server. |
| `i_channel_subscribe_power` | The bot must be able to subscribe channels, so that clients can be found in those channels. |
| `b_virtualserver_servergroup_list` | Allow the bot to get the list of available servergroups on your virtual server. |
| `i_client_kick_from_server_power` | Allow the bot to kick clients from the server. |
| `i_client_private_textmessage_power` | The bot will send in specific cases a private message to the client. If somebody wants to know the plugin version for example. |

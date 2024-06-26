# Writing plugins

A feature of this bot is that it is easily extendable.

To write your own plugin you need to do the following things:

1. Copy the `plugin_template` plugin and name the folder as descriptive as possible for your plugin
2. Update the template Python script to your needs
3. Update and fill the `README.md` within your plugin folder
4. Optionally link your plugin in the main `README.md` of this project under `Available Plugins`, if you are planning to publish it
2. Add your new plugin to the `config.ini`

That's it. The plugin sends regulary a "Hello World!" message to all connected clients. You can build up on that.

You can see further example plugins in the [`modules/` directory](/modules/).

Please check out the [ts3API](https://github.com/Murgeye/teamspeak3-python-api) documentation for available API functions, properties, error handling etc..

## Adding setup and exit methods

Upon loading a plugin the ModuleLoader calls any method marked as `@setup_plugin` in the plugin.

```
from module_loader import setup_plugin
# ...
@setup_plugin
def setup_module(ts3bot):
  #Do something, save the bot reference, etc
  pass
```

Upon unloading a module (usually if the bot is closed etc) the ModuleLoader calls any method
marked as `@exit_plugin` in the plugin.

```
from module_loader import exit_plugin
# ...
@exit_plugin
def exit_module():
  #Do something, save your state, etc
  pass
```

## Adding a text command

You can register your plugin to specific commands (starting with !) send via private message
by using the `@command` decorator.

The following code example registers the `test_command` function for the command `!test1` and `!test2`:

```
from module_loader import command, group
# ...
@command('test1','test2')
@group('Server Admin')
def test_command(sender, msg):
  print("test")
```

You can register a function for as many commands as you want and you can register as many functions for a command as you want.

The `sender` argument is the client id of the user who sent the command and `msg` contains the whole text
of the private message.

### `@group` permissions

The `@group` decorator specifies which Server Groups are allowed to use this function via textcommands.

You can use regex here so you can do things like `@group('.*Admin.*','Moderator',)` to allow all groups containing the word `Admin` and the `Moderator` group to send this command.

`@group('.*')` allows everybody to use a command. If you don't use `@group` the default will be to allow access to `Server Admin`.

## Listening for events

You can register a function in your plugin to listen for specific server events by using the `@event` decorator.

The following code snippet registers the `inform_enter` function as a listener for the `Events.ClientEnteredEvent`:

```
from module_loader import event
from ts3API.Events import ClientEnteredEvent
# ...
@event(Events.ClientEnteredEvent)
def inform_enter(event):
  print("Client with id " + event.client_id + " left.")
```

You can register a function for multiple events by passing a list of event types to the decorator. To learn more about the events look at the ts3API.Events module.

## PyLint (Code Analysis)

This project uses [PyLint](https://www.pylint.org/) for analysing the code.

Please ensure, that your code, which you develop does not lower the rating.

Install PyLint:

```
pip install pylint
```

Run PyLint:

```
pylint --rcfile=pylintrc $(find . -type f -name "*.py")
```

The `pylintrc` defines the respective PyLint configuration.

## Black Formatter (Code Style)

This project uses the [Black](https://black.readthedocs.io/en/stable/) formatter for code-style.

Please checkout the official documentation of Black: https://black.readthedocs.io/en/stable/getting_started.html

The `pyproject.toml` defines the respective Black formatter configuration.

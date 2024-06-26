# standard imports
import importlib
import logging
import sys

# local imports
from command_handler import CommandHandler
from event_handler import EventHandler

setups = []
exits = []
plugin_modules = {}
EVENT_HANDLER: "EventHandler"
COMMAND_HANDLER: "CommandHandler"

# configure logger
CLASS_NAME = "ModuleLoader"
logger = logging.getLogger(CLASS_NAME)
logger.propagate = 0
logger.setLevel(logging.INFO)
file_handler = logging.FileHandler(f"logs/{CLASS_NAME.lower()}.log", mode="a+")
formatter = logging.Formatter("%(asctime)s: %(levelname)s: %(message)s")
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)
logger.info("Configured %s logger", str(CLASS_NAME))
logger.propagate = 0


# We really really want to catch all Exception here to prevent a bad module crashing the
# whole Bot
# noinspection PyBroadException,PyPep8
def load_modules(bot, config):
    """
    Load modules specified in the Plugins section of config.ini.
    :param bot: Bot to pass to the setup function of the modules
    :param config: Main bot config with plugins section
    """
    global EVENT_HANDLER, COMMAND_HANDLER
    plugins = config.pop("Plugins")
    EVENT_HANDLER = bot.event_handler
    COMMAND_HANDLER = bot.command_handler

    for plugin in plugins.items():
        try:
            plugin_modules[plugin[0]] = importlib.import_module(
                f"modules.{plugin[1]}", package="modules"
            )
            plugin_modules[plugin[0]].pluginname = plugin[1]
            logger.info("Loaded module %s", str(plugin[1]))
        except ImportError:
            logger.exception(
                "Error while loading plugin %s from modules. %s",
                str(plugin[0]),
                str(plugin[1]),
            )
            raise
        except KeyError:
            logger.exception("Error while loading plugin from modules: %s", str(plugin))
            raise

    # Call all registered setup functions
    for setup_func in setups:
        try:
            # `SomeModule` => `SomeModule`
            plugin_name = sys.modules.get(setup_func.__module__).pluginname
            if plugin_name.count(".") == 1:
                # `SomeModule.main` => `SomeModule`
                plugin_name = plugin_name.split(".")[0]
        except AttributeError:
            logger.exception("Error while setting up the module %s.", str(plugin_name))
            raise

        if plugin_name in config:
            plugin_config = config.pop(plugin_name)
            logger.info("%s plugin config: %s", str(plugin_name), str(plugin_config))
            setup_func(ts3bot=bot, **plugin_config)
        else:
            logger.info(
                "%s plugin config: Unconfigured, using plugin defaults.",
                str(plugin_name),
            )
            setup_func(bot)


def setup_plugin(function):
    """
    Decorator for registering the setup function of a module.
    :param function: Function to register as setup
    :return:
    """
    setups.append(function)
    return function


def event(*event_types):
    """
    Decorator to register a function as an eventlistener for the event types specified in
    event_types.
    :param event_types: Event types to listen to
    :type event_types: TS3Event
    """

    def register_observer(function):
        for event_type in event_types:
            EVENT_HANDLER.add_observer(function, event_type)
        return function

    return register_observer


def command(*command_list):
    """
    Decorator to register a function as a handler for text commands.
    :param command_list: Commands to handle.
    :type command_list: str
    :return:
    """

    def register_command(function):
        for text_command in command_list:
            COMMAND_HANDLER.add_handler(function, text_command)
        return function

    return register_command


def group(*groups):
    """
    Decorator to specify which groups are allowed to use the commands specified for this function.
    :param groups: List of server groups that are allowed to use the commands associated with this
    function.
    :type groups: str
    """

    def save_allowed_groups(func):
        func.allowed_groups = groups
        return func

    return save_allowed_groups


def exit_plugin(function):
    """
    Decorator to mark a function to be called upon module exit.
    :param function: Exit function to call.
    """
    exits.append(function)


# We really really want to catch all Exception here to prevent a bad module preventing everything
# else from exiting
# noinspection PyBroadException
def exit_all():
    """
    Exit all modules by calling their exit function.
    """
    for exit_func in exits:
        try:
            exit_func()
        except BaseException:
            logger.exception("Error while exiting the module %s.", str(exit_func))

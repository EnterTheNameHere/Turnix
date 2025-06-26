import logging
logger = logging.getLogger(__name__)

class DriverRegistry:
    def __init__(self):
        self.driverTypes: dict[str, dict[str, type]] = {}

    def registerDriver(self, driverType: str, driverName: str, driverClass: type):
        if driverType not in self.driverTypes:
            self.driverTypes[driverType] = {}
        if driverName in self.driverTypes[driverType]:
            logger.warning(f"Driver '{driverClass.__name__}' already registered for '{driverType}'. Overwritting.")
        
        logger.info(f"Registering driver '{driverClass.__name__}' for '{driverType}'")
        self.driverTypes[driverType][driverName] = driverClass

    def getDriver(self, driverType: str, driverName: str) -> type:
        if driverType not in self.driverTypes:
            logger.error(f"Unknown driver type '{driverType}' and name '{driverName}'")
            raise ValueError(f"Unknown driver type '{driverType}' and name '{driverName}'")
        if driverName not in self.driverTypes[driverType]:
            logger.error(f"Unknown driver name '{driverName}'")
            raise ValueError(f"Unknown driver name '{driverName}' for type '{driverType}'")
        return self.driverTypes[driverType][driverName]

    def listDrivers(self, driverType: str) -> list[str]:
        return list(self.driverTypes.get(driverType, {}).keys())

# Singleton
driverRegistry = DriverRegistry()

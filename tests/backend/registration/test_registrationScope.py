from backend.registration import RegistrationRegistry, RegistrationScope


def test_scope_hides_until_publish_and_preserves_owner():
    registry = RegistrationRegistry[object]()
    scope = RegistrationScope()
    scope.register(registry, ownerId="entry-a", name="alpha", value=1)

    try:
        registry.require("alpha")
    except LookupError:
        pass
    else:
        raise AssertionError("provisional registration became visible")

    scope.publish()
    registration = registry.require("alpha")
    assert registration.ownerId == "entry-a"
    assert registration.value == 1


def test_scope_preflight_prevents_partial_publication():
    registry = RegistrationRegistry[object]()
    first = RegistrationScope()
    first.register(registry, ownerId="existing", name="taken", value=1)
    first.publish()

    scope = RegistrationScope()
    scope.register(registry, ownerId="new-a", name="new", value=2)
    scope.register(registry, ownerId="new-b", name="taken", value=3)

    try:
        scope.publish()
    except RuntimeError:
        pass
    else:
        raise AssertionError("collision should fail publication")

    try:
        registry.require("new")
    except LookupError:
        pass
    else:
        raise AssertionError("Pack publication was partial")

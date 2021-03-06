import datetime
import logging

from django.core.exceptions import NON_FIELD_ERRORS

from google.appengine.ext import db
from google.appengine.api.datastore import Key, Delete
from google.appengine.datastore.datastore_rpc import TransactionOptions

from .unique_utils import unique_identifiers_from_entity
from .utils import key_exists
from djangae.db.backends.appengine.dbapi import IntegrityError

from django.conf import settings

DJANGAE_LOG = logging.getLogger("djangae")


def constraint_checks_enabled(model_or_instance):
    """
        Returns true if constraint checking is enabled on the model
    """

    opts = getattr(model_or_instance, "Djangae", None)
    if opts:
        if hasattr(opts, "disable_constraint_checks"):
            if opts.disable_constraint_checks:
                return False
            else:
                return True

    return not getattr(settings, "DJANGAE_DISABLE_CONSTRAINT_CHECKS", False)



class KeyProperty(db.Property):
    """A property that stores a datastore.Key reference to another object.
        Think of this as a Django GenericForeignKey which returns only the PK value, not the whole
        object, or a db.ReferenceProperty which can point to any model kind, and only returns the Key.
    """

    def validate(self, value):
        if value is None or isinstance(value, Key):
            return value
        raise ValueError("KeyProperty only accepts datastore.Key or None")


class UniqueMarker(db.Model):
    instance = KeyProperty()
    created = db.DateTimeProperty(required=True, auto_now_add=True)

    @staticmethod
    def kind():
        return "__unique_marker"


def acquire_identifiers(identifiers, entity_key):
    @db.transactional(propagation=TransactionOptions.INDEPENDENT, xg=True)
    def acquire_marker(identifier):
        identifier_key = Key.from_path(UniqueMarker.kind(), identifier)

        marker = UniqueMarker.get(identifier_key)
        if marker:
            # If the marker instance is None, and the marker is older then 5 seconds then we wipe it out
            # and assume that it's stale.
            if not marker.instance and (datetime.datetime.utcnow() - marker.created).seconds > 5:
                marker.delete()
            elif marker.instance and marker.instance != entity_key and key_exists(marker.instance):
                raise IntegrityError("Unable to acquire marker for %s" % identifier)
            else:
                # The marker is ours anyway
                return marker

        marker = UniqueMarker(
            key=identifier_key,
            instance=entity_key if entity_key.id_or_name() else None,  # May be None if unsaved
            created=datetime.datetime.utcnow()
        )
        marker.put()
        return marker

    markers = []
    try:
        for identifier in identifiers:
            markers.append(acquire_marker(identifier))
            DJANGAE_LOG.debug("Acquired unique marker for %s", identifier)
    except:
        release_markers(markers)
        DJANGAE_LOG.debug("Due to an error, deleted markers %s", markers)
        raise
    return markers


def get_markers_for_update(model, old_entity, new_entity):
    """
        Given an old entity state, and the new state, updates the identifiers
        appropriately. Should be called before saving the new_state
    """
    old_ids = set(unique_identifiers_from_entity(model, old_entity, ignore_pk=True))
    new_ids = set(unique_identifiers_from_entity(model, new_entity, ignore_pk=True))

    to_release = old_ids - new_ids
    to_acquire = new_ids - old_ids

    return to_acquire, to_release


def update_instance_on_markers(entity, markers):

    @db.transactional(propagation=TransactionOptions.INDEPENDENT)
    def update(marker, instance):
        marker = UniqueMarker.get(marker.key())
        if not marker:
            return

        marker.instance = instance
        marker.put()

    instance = entity.key()
    for marker in markers:
        update(marker, instance)


def acquire_bulk(model, entities):
    markers = []
    try:
        for entity in entities:
            markers.append(acquire(model, entity))

    except:
        for m in markers:
            release_markers(m)
        raise
    return markers


def acquire(model, entity):
    """
        Given a model and entity, this tries to acquire unique marker locks for the instance. If the locks already exist
        then an IntegrityError will be thrown.
    """

    identifiers = unique_identifiers_from_entity(model, entity, ignore_pk=True)
    return acquire_identifiers(identifiers, entity.key())


def release_markers(markers):
    @db.transactional(propagation=TransactionOptions.INDEPENDENT)
    def delete(marker):
        Delete(marker.key())

    [delete(x) for x in markers]


def release_identifiers(identifiers):

    @db.non_transactional
    def delete():
        keys = [Key.from_path(UniqueMarker.kind(), x) for x in identifiers]
        Delete(keys)

    delete()
    DJANGAE_LOG.debug("Deleted markers with identifiers: %s", identifiers)


def release(model, entity):
    identifiers = unique_identifiers_from_entity(model, entity, ignore_pk=True)
    release_identifiers(identifiers)


class UniquenessMixin(object):
    """ Mixin overriding the methods checking value uniqueness.

    For models defining unique constraints this mixin should be inherited from.
    When iterable (list or set) fields are marked as unique it must be used.
    This is a copy of Django's implementation, save for the part marked by the comment.
    """
    def _perform_unique_checks(self, unique_checks):
        errors = {}
        for model_class, unique_check in unique_checks:
            lookup_kwargs = {}
            for field_name in unique_check:
                f = self._meta.get_field(field_name)
                lookup_value = getattr(self, f.attname)
                if lookup_value is None:
                    continue
                if f.primary_key and not self._state.adding:
                    continue

                ##########################################################################
                # This is the only modification to Django's native implementation of this method;
                # we conditionally build a __in lookup if the value is an iterable.
                lookup = str(field_name)
                if isinstance(lookup_value, (list, set, tuple)):
                    lookup = "%s__in" % lookup

                lookup_kwargs[lookup] = lookup_value
                ##########################################################################
                # / end of changes

            if len(unique_check) != len(lookup_kwargs):
                continue
            qs = model_class._default_manager.filter(**lookup_kwargs)
            model_class_pk = self._get_pk_val(model_class._meta)
            if not self._state.adding and model_class_pk is not None:
                qs = qs.exclude(pk=model_class_pk)
            if qs.exists():
                if len(unique_check) == 1:
                    key = unique_check[0]
                else:
                    key = NON_FIELD_ERRORS
                errors.setdefault(key, []).append(self.unique_error_message(model_class, unique_check))
        return errors

from hashlib import md5
from django.db import models
from google.appengine.api import datastore

from .models import UniqueAction, encode_model
from djangae.test import TestCase, process_task_queues
from djangae.db.constraints import UniqueMarker, UniquenessMixin


class TestModel(UniquenessMixin, models.Model):
    name = models.CharField(max_length=32, unique=True)
    counter1 = models.IntegerField()
    counter2 = models.IntegerField()

    class Meta:
        unique_together = [('counter1', 'counter2')]


class MapperTests(TestCase):

    def setUp(self):
        super(MapperTests, self).setUp()
        self.i1 = TestModel.objects.create(name="name1", counter1=1, counter2=1)
        self.i2 = TestModel.objects.create(name="name3", counter1=1, counter2=2)

    def test_check_ok(self):
        # A check should produce no errors.
        UniqueAction.objects.create(action_type="check", model=encode_model(TestModel))
        process_task_queues()

        a = UniqueAction.objects.get()
        self.assertEqual(a.status, "done")
        self.assertEqual(0, a.actionlog_set.count())

    def test_check_missing_markers(self):
        marker1 = "{}|name:{}".format(TestModel._meta.db_table, md5(self.i2.name).hexdigest())
        marker_key = datastore.Key.from_path(UniqueMarker.kind(), marker1)
        datastore.Delete(marker_key)
        UniqueAction.objects.create(action_type="check", model=encode_model(TestModel))
        process_task_queues()

        a = UniqueAction.objects.get()
        self.assertEqual(a.status, "done")
        self.assertEqual(1, a.actionlog_set.count())
        error = a.actionlog_set.all()[0]
        instance_key = datastore.Key.from_path(TestModel._meta.db_table, self.i2.pk)
        self.assertEqual(error.log_type, "missing_marker")
        self.assertEqual(error.instance_key, str(instance_key))
        self.assertEqual(error.marker_key, str(marker_key))

    def test_check_missing_instance_attr(self):
        marker1 = "{}|name:{}".format(TestModel._meta.db_table, md5(self.i2.name).hexdigest())
        marker_key = datastore.Key.from_path(UniqueMarker.kind(), marker1)
        marker = datastore.Get(marker_key)
        marker['instance'] = None
        datastore.Put(marker)

        UniqueAction.objects.create(action_type="check", model=encode_model(TestModel))
        process_task_queues()

        a = UniqueAction.objects.get()
        self.assertEqual(a.status, "done")
        self.assertEqual(1, a.actionlog_set.count())
        error = a.actionlog_set.all()[0]
        instance_key = datastore.Key.from_path(TestModel._meta.db_table, self.i2.pk)
        self.assertEqual(error.log_type, "missing_instance")
        self.assertEqual(error.instance_key, str(instance_key))
        self.assertEqual(error.marker_key, str(marker_key))

    def test_repair_missing_markers(self):
        instance_key = datastore.Key.from_path(TestModel._meta.db_table, self.i2.pk)
        marker1 = "{}|name:{}".format(TestModel._meta.db_table, md5(self.i2.name).hexdigest())
        marker_key = datastore.Key.from_path(UniqueMarker.kind(), marker1)
        datastore.Delete(marker_key)
        UniqueAction.objects.create(action_type="repair", model=encode_model(TestModel))
        process_task_queues()

        a = UniqueAction.objects.get()
        self.assertEqual(a.status, "done")
        self.assertEqual(0, a.actionlog_set.count())
        # Is the missing marker restored?
        marker = datastore.Get(marker_key)
        self.assertTrue(marker)
        self.assertTrue(isinstance(marker["instance"], datastore.Key))
        self.assertEqual(instance_key, marker["instance"])

    def test_check_old_style_marker(self):
        instance_key = datastore.Key.from_path(TestModel._meta.db_table, self.i2.pk)

        marker1 = "{}|name:{}".format(TestModel._meta.db_table, md5(self.i2.name).hexdigest())
        marker_key = datastore.Key.from_path(UniqueMarker.kind(), marker1)
        marker = datastore.Get(marker_key)
        marker['instance'] = str(instance_key) #Make the instance a string
        datastore.Put(marker)

        UniqueAction.objects.create(action_type="check", model=encode_model(TestModel))
        process_task_queues()

        a = UniqueAction.objects.get()
        self.assertEqual(a.status, "done")
        self.assertEqual(1, a.actionlog_set.count())
        error = a.actionlog_set.all()[0]

        self.assertEqual(error.log_type, "old_instance_key")
        self.assertEqual(error.instance_key, str(instance_key))
        self.assertEqual(error.marker_key, str(marker_key))

    def test_repair_old_style_marker(self):
        instance_key = datastore.Key.from_path(TestModel._meta.db_table, self.i2.pk)

        marker1 = "{}|name:{}".format(TestModel._meta.db_table, md5(self.i2.name).hexdigest())
        marker_key = datastore.Key.from_path(UniqueMarker.kind(), marker1)
        marker = datastore.Get(marker_key)
        marker['instance'] = str(instance_key) #Make the instance a string
        datastore.Put(marker)

        UniqueAction.objects.create(action_type="repair", model=encode_model(TestModel))
        process_task_queues()

        a = UniqueAction.objects.get()
        self.assertEqual(a.status, "done")
        self.assertEqual(0, a.actionlog_set.count())
        marker = datastore.Get(marker_key)
        self.assertTrue(marker)
        self.assertEqual(marker['instance'], instance_key)

    def test_repair_missing_instance_attr(self):
        instance_key = datastore.Key.from_path(TestModel._meta.db_table, self.i2.pk)
        marker1 = "{}|name:{}".format(TestModel._meta.db_table, md5(self.i2.name).hexdigest())
        marker_key = datastore.Key.from_path(UniqueMarker.kind(), marker1)
        marker = datastore.Get(marker_key)
        marker['instance'] = None
        datastore.Put(marker)

        UniqueAction.objects.create(action_type="repair", model=encode_model(TestModel))
        process_task_queues()

        a = UniqueAction.objects.get()
        self.assertEqual(a.status, "done")
        self.assertEqual(0, a.actionlog_set.count())
        marker = datastore.Get(marker_key)
        self.assertTrue(marker)
        self.assertEqual(marker['instance'], instance_key)

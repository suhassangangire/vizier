"""Classes for starting the Vizier Service."""

from concurrent import futures
import datetime
import time

import attr
import grpc
import portpicker
from vizier.service import datastore
from vizier.service import pythia_server
from vizier.service import pythia_service_pb2_grpc
from vizier.service import stubs_util
from vizier.service import vizier_server
from vizier.service import vizier_service_pb2_grpc


@attr.define
class DefaultVizierService:
  """Vizier service which runs Pythia and Vizier Server in the same process."""
  _host: str = attr.field(init=True, default='localhost')
  _database_url: str = attr.field(
      init=True, default=vizier_server.SQL_MEMORY_URL, kw_only=True)
  _early_stop_recycle_period: datetime.timedelta = attr.field(
      init=False, default=datetime.timedelta(seconds=0.1))
  _port: int = attr.field(init=False, factory=portpicker.pick_unused_port)
  _pythia_port: int = attr.field(
      init=False, factory=portpicker.pick_unused_port)
  _servicer: vizier_server.VizierService = attr.field(init=False)
  _pythia_servicer: pythia_server.PythiaService = attr.field(init=False)
  _server: grpc.Server = attr.field(init=False)
  _pythia_server: grpc.Server = attr.field(init=False)
  stub: vizier_service_pb2_grpc.VizierServiceStub = attr.field(init=False)
  pythia_stub: pythia_service_pb2_grpc.PythiaServiceStub = attr.field(
      init=False)

  @property
  def datastore(self) -> datastore.DataStore:
    return self._servicer.datastore

  @property
  def endpoint(self) -> str:
    return f'{self._host}:{self._port}'

  @property
  def pythia_endpoint(self) -> str:
    return f'{self._host}:{self._pythia_port}'

  def __init__(self):
    self.__attrs_init__()

    # Setup Vizier server.
    self._servicer = vizier_server.VizierService(
        database_url=self._database_url,
        early_stop_recycle_period=self._early_stop_recycle_period)
    self._server = grpc.server(futures.ThreadPoolExecutor(max_workers=30))
    vizier_service_pb2_grpc.add_VizierServiceServicer_to_server(
        self._servicer, self._server)
    self._server.add_secure_port(self.endpoint, grpc.local_server_credentials())
    self._server.start()
    self.stub = stubs_util.create_vizier_server_stub(self.endpoint)

    # Setup Pythia server.
    self._pythia_servicer = pythia_server.PythiaService()
    # `max_workers=1` is used since we can only run one Pythia thread at a time.
    self._pythia_server = grpc.server(futures.ThreadPoolExecutor(max_workers=1))
    pythia_service_pb2_grpc.add_PythiaServiceServicer_to_server(
        self._pythia_servicer, self._pythia_server)
    self._pythia_server.add_secure_port(self.pythia_endpoint,
                                        grpc.local_server_credentials())
    self._pythia_server.start()
    self.pythia_stub = stubs_util.create_pythia_server_stub(
        self.pythia_endpoint)

    # Connect Vizier and Pythia servers together.
    self._servicer.connect_to_pythia(self.pythia_endpoint)
    self._pythia_servicer.connect_to_vizier(self.endpoint)

  def wait_for_early_stop_recycle_period(self) -> None:
    time.sleep(self._early_stop_recycle_period.total_seconds())
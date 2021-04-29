from abc import (
    ABC,
    abstractmethod
)

from eth.chains.base import (
    MiningChain,
    Chain,
    BaseChain,
)

from eth.validation import (
    validate_block_number
)

from eth.abc import (
    AtomicDatabaseAPI,
    BlockHeaderAPI,
)


from eth.exceptions import (
    CanonicalHeadNotFound,
)

import operator
import random
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    Sequence,
    Tuple,
    Type,
)

import logging

from eth_typing import (
    Address,
    BlockNumber,
    Hash32,
)
from eth_utils import (
    encode_hex,
)
from eth_utils.toolz import (
    concatv,
    sliding_window,
)

from eth._utils.db import (
    apply_state_dict,
)
from eth._utils.datatypes import (
    Configurable,
)
from eth._utils.headers import (
    compute_gas_limit_bounds,
)
from eth._utils.rlp import (
    validate_imported_block_unchanged,
)
from eth.abc import (
    BlockAPI,
    BlockAndMetaWitness,
    MiningChainAPI,
    AtomicDatabaseAPI,
    BlockHeaderAPI,
    BlockImportResult,
    ChainAPI,
    ChainDatabaseAPI,
    ConsensusContextAPI,
    VirtualMachineAPI,
    ReceiptAPI,
    ComputationAPI,
    StateAPI,
    SignedTransactionAPI,
    UnsignedTransactionAPI,
    VmTracerAPI,
)
from eth.consensus import (
    ConsensusContext,
)
from eth.constants import (
    EMPTY_UNCLE_HASH,
    MAX_UNCLE_DEPTH,
)

from eth.db.chain import (
    ChainDB,
)
from eth.db.header import (
    HeaderDB,
)

from eth.estimators import (
    get_gas_estimator,
)
from eth.exceptions import (
    HeaderNotFound,
    TransactionNotFound,
    VMNotFound,
)

from eth.rlp.headers import (
    BlockHeader,
)

from eth.typing import (
    AccountState,
    HeaderParams,
    StaticMethod,
)

from eth.validation import (
    validate_block_number,
    validate_uint256,
    validate_word,
    validate_vm_configuration,
)
from eth.vm.chain_context import ChainContext

from eth._warnings import catch_and_ignore_import_warning
with catch_and_ignore_import_warning():
    from eth_utils import (
        to_set,
        ValidationError,
    )
    from eth_utils.toolz import (
        assoc,
        compose,
        groupby,
        iterate,
        take,
    )


# TODO move this to package abc
class OriginChainDataLoaderAPI(ABC):
    @abstractmethod
    def get_canonical_head(self) -> BlockHeaderAPI:
        ...

    @abstractmethod
    def get_block_header_by_hash(self, block_hash: Hash32) -> BlockHeaderAPI:
        ...

    @abstractmethod
    def get_canonical_block_header_by_number(self, block_number: BlockNumber) -> BlockHeaderAPI:
        ...


class ForkChain(Chain):
    fork_block_number: int = None
    origin_loader: OriginChainDataLoaderAPI = None

    def __init__(self,
                 base_db: AtomicDatabaseAPI) -> None:
        super().__init__(base_db)

        validate_block_number(self.fork_block_number, 'Fork Block Number')
        if self.origin_loader is None:
            raise ValidationError('origin_loader is None')

        # Loading the current head and persisting to the local DB.
        # Note that initially a fork chain consists of the genesis block and the header of the
        # head block only. All other data are loaded on demand.
        origin_head = self.origin_loader.get_canonical_block_header_by_number(BlockNumber(self.fork_block_number))
        chaindb = self.get_chaindb_class()(base_db)

        if chaindb.get_canonical_head() != origin_head:
            chaindb.persist_checkpoint_header(origin_head, origin_head.difficulty)

        # self.header = self.ensure_header(None)

    def get_block_header_by_hash(self, block_hash: Hash32) -> BlockHeaderAPI:
        validate_word(block_hash, title="Block Hash")
        try:
            return self.chaindb.get_block_header_by_hash(block_hash)
        except HeaderNotFound:
            pass

        # Header not found in local DB. Load from origin.
        return self.origin_loader.get_block_header_by_hash(block_hash)

    def get_canonical_block_header_by_number(self, block_number: BlockNumber) -> BlockHeaderAPI:
        try:
            return self.chaindb.get_canonical_block_header_by_number(block_number)
        except HeaderNotFound:
            pass

        # Header not found in local DB. Load from origin.
        return self.origin_loader.get_canonical_block_header_by_number(block_number)

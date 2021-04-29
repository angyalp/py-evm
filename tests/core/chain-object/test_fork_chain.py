import pytest

from eth.typing import (
    Hash32,
    BlockNumber,
)

from eth_utils import (
    decode_hex,
    to_canonical_address,
    to_wei,
)

from eth.abc import (
    BlockHeaderAPI,
    ChainAPI
)

from eth.consensus import NoProofConsensus

from eth import constants

from eth.chains import (
    Chain,
)

from eth.chains.base import (
    MiningChain,
)

from eth.chains.fork import (
    ForkChain,
    OriginChainDataLoaderAPI
)

from eth.db.atomic import (
    AtomicDB,
)

from eth_keys import keys

from eth.tools.factories.transaction import new_transaction


class OriginChainDataLoaderHelper(OriginChainDataLoaderAPI, MiningChain):
    def get_canonical_head(self) -> BlockHeaderAPI:
        return super(MiningChain, self).get_canonical_head()

    def get_block_header_by_hash(self, block_hash: Hash32) -> BlockHeaderAPI:
        return super(MiningChain, self).get_block_header_by_hash(block_hash)

    def get_canonical_block_header_by_number(self, block_number: BlockNumber) -> BlockHeaderAPI:
        return super(MiningChain, self).get_canonical_block_header_by_number(block_number)


def import_block_without_validation(chain, block):
    return super(type(chain), chain).import_block(block, perform_validation=False)


def _fork_chain_without_block_validation(VM, base_db, genesis_state, account_fixtures, fork_at_head):
    """
    Return a Chain object containing just the genesis block.

    The Chain's state includes one funded account, which can be found in the
    funded_address in the chain itself.
    """
    genesis_params = {
        "coinbase": constants.GENESIS_COINBASE,
        "difficulty": constants.GENESIS_DIFFICULTY,
        "extra_data": constants.GENESIS_EXTRA_DATA,
        "gas_limit": 3141592,
        "mix_hash": constants.GENESIS_MIX_HASH,
        "nonce": constants.GENESIS_NONCE,
        "block_number": constants.GENESIS_BLOCK_NUMBER,
        "parent_hash": constants.GENESIS_PARENT_HASH,
        "timestamp": 1501851927,
    }

    common_chain_config = {
        'vm_configuration': ((constants.GENESIS_BLOCK_NUMBER, VM.configure(consensus_class=NoProofConsensus)),),
        'chain_id': 1337,
        'import_block': import_block_without_validation,
        'validate_block': lambda self, block: None,
    }

    loader_config = {
        **common_chain_config,
        '__name__': 'OriginLoaderChain'
    }
    loader_klass = OriginChainDataLoaderHelper.configure(**loader_config)
    loader = loader_klass.from_genesis(AtomicDB(), genesis_params, genesis_state)

    # Adding 3 transactions in separate blocks.
    funded_address_private_key, funded_address, \
    address1_private_key, address1, \
    address2_private_key, address2, \
    address3_private_key, address3 = account_fixtures

    for dest_addr in (address1, address2, address3):
        tx = new_transaction(loader.get_vm(), funded_address, dest_addr, to_wei(1, 'ether'), funded_address_private_key)
        loader.apply_transaction(tx)
        loader.mine_block()

    fork_block_number = loader.get_canonical_head().block_number
    if not fork_at_head:
        fork_block_number = max(constants.GENESIS_BLOCK_NUMBER, fork_block_number-1)

    config = {
        **common_chain_config,
        '__name__': 'TestChain',
        'fork_block_number': fork_block_number,
        'origin_loader': loader
    }
    klass = ForkChain.configure(**config)
    chain = klass.from_genesis(base_db, genesis_params, genesis_state)
    return chain, loader


@pytest.fixture
def fork_chain_without_block_validation(VM, base_db, genesis_state, account_fixtures, fork_at_head):
    return _fork_chain_without_block_validation(VM, base_db, genesis_state, account_fixtures, fork_at_head)


@pytest.fixture
def fork_chain(fork_chain_without_block_validation):
    return fork_chain_without_block_validation


# Test configurations:
# - No modification done at fork:
# -- Fork at head: Origin chain: genesis + 3 block, Fork at 4
# -- Fork before head: Origin chain: genesis + 3 block, Fork at 3
# - Modifications done at fork:
# -- TODO

# Proof of Concept:
# TODO: Latszik, hogy minden lekerdezes a chain.state-en keresztul megy, igy lehet azt kellene kozvetlenul tesztelni
#  nem pedig a chain-t, ami kozvetve hivja azt.
#  Amikor egy account-ra szukseg van es modositjuk, akkor a modositott adatot le kell menteni a local DB-be, mivel ez
#  elteres az origin-hez kepest. Ket dolgot tehetunk: A teljes account state-et szinkronizaljuk, vagy csak azt amit
#  modositunk. Teljes account state: balance, nonce, code, storage. A code es a storage lehet nagy, ezert
#  nem hatekony/felesleges szinkronizalni, ha nem hasznaljuk. A teljes account state letoltese azert sem megoldhato,
#  mert web3 API-t akarunk hasznalni az origin adatok eleresehez, es ebben nincs mod a teljes storage lekeresere.
#  Ha csak a modositott state-et mentjuk a DB-be, akkor ez a kovetkezot jelenti:
#  - Tavoli account (ami az origin-ben keletkezett es nem a fork-on) code sosem lesz a DB-ben, de figyelni kell, hogy
#     az account torlest megfeleloen taroljuk. Tehat valahanyszor kell a code, lekerjuk. Ennek a hatekonnya tetele az
#     origin data loaderen mulik.
#  - Tavoli account storage slot csak az lesz a DB-ben, amit modositottunk/letrehoztunk a forkon. Valahanyszor kell egy
#     slot, lekerjuk (amennyiben nincs meg nekunk). Ennek a hatekonnya tetele az origin data loaderen mulik.
#  Meg kell tudnunk kulonboztetni azt, hogy egy adott adat a forkon nincs meg, vagy pedig mar megvolt a forkon, de
#  el lett tavolitva. Elobbi esetben az originrol kell lekerni, utobbi esetben nem. A kovetkezo adatoknal fordulhat ez
#  elo:
#  - Block es tranzakcio: Itt az elso eset egy specialis valtozatat kell kezelni: Nincs meg a fork-on, megvan az
#     originen, de nem tartozik a forkhoz: A blokk/tx a fork blokk utan keletkezett.
#  - Account: Ha torlunk egy accountot a forkon, akkor ezt fel kell ismerni. Account code torlese is ide tartozik,
#     mivel code-ot csak az accounttal egyutt lehet torolni.
#  - Account storage slot: Meg kell tudni kulonboztetni, hogy az adott slot a forkon lett torolve (0-ra allitva),
#     vagy pedig nem is volt meg szinkronizalva, ezert le kell kerni az origintol.

def test_chain_head_fork(fork_chain):
    fork_chain, origin = fork_chain

    fork_block_num = fork_chain.get_canonical_head().block_number
    print(f'Origin head: {origin.get_canonical_head().block_number}, '
          f'Fork block: {fork_block_num}')

    # Header API

    # Headers must match
    for block_num in range(fork_block_num+1):
        origin_header = origin.get_canonical_block_header_by_number(block_num)
        fork_header = fork_chain.get_canonical_block_header_by_number(block_num)
        assert origin_header == fork_header

    # Canonical head must be the fork block.
    header = fork_chain.get_canonical_block_header_by_number(fork_block_num)
    assert fork_chain.get_canonical_head() == header

    # TODO implement and test these
    # def get_block_header_by_hash(self, block_hash: Hash32) -> BlockHeaderAPI:
    # def get_score(self, block_hash: Hash32) -> int:

    # Block API
    # def get_block(self) -> BlockAPI:
    # def get_block_by_hash(self, block_hash: Hash32) -> BlockAPI:
    # def get_block_by_header(self, block_header: BlockHeaderAPI) -> BlockAPI:
    # def get_canonical_block_by_number(self, block_number: BlockNumber) -> BlockAPI:
    # def get_canonical_block_hash(self, block_number: BlockNumber) -> Hash32:

    # Ez mar modosit: def build_block_with_transactions(
    # Execution API
    # Validation API?

    # Transaction API
    # def get_canonical_transaction_index(self, transaction_hash: Hash32) -> Tuple[BlockNumber, int]:
    # def get_canonical_transaction(self, transaction_hash: Hash32) -> SignedTransactionAPI:
    # def get_canonical_transaction_by_index(self,
    # def get_transaction_receipt(self, transaction_hash: Hash32) -> ReceiptAPI:
    # def get_transaction_receipt_by_index(self,


def test_state(fork_chain, account_fixtures):
    fork_chain, origin = fork_chain

    funded_address_private_key, funded_address, \
    address1_private_key, address1, \
    address2_private_key, address2, \
    address3_private_key, address3 = account_fixtures

    fork_block_num = fork_chain.get_canonical_head().block_number
    print(f'Origin head: {origin.get_canonical_head().block_number}, '
          f'Fork block: {fork_block_num}')

    head = fork_chain.ensure_header()
    vm = fork_chain.get_vm(head)
    assert vm.state.get_balance(address1) == to_wei(1, 'ether')


# def test_chain_tx(fork_chain, account_fixtures):
#     fork_chain, origin = fork_chain
#
#     funded_address_private_key, funded_address, \
#     address1_private_key, address1, \
#     address2_private_key, address2, \
#     address3_private_key, address3 = account_fixtures
#
#     fork_block_num = fork_chain.get_canonical_head().block_number
#     print(f'Origin head: {origin.get_canonical_head().block_number}, '
#           f'Fork block: {fork_block_num}')
#
#     priv_key = keys.PrivateKey(
#         decode_hex('0x45a915e4d060149eb4365960e6a7a45f334393093061116b197e3240065ff2ca')
#     )
#     dest_addr = priv_key.public_key.to_canonical_address()
#
#     # nonce = vm.state.get_nonce(from_)  # TODO implement get_nonce in forkchain
#     head = fork_chain.ensure_header()
#     vm = fork_chain.get_vm(head)
#     base_block = vm.get_block()
#
#     tx = vm.create_unsigned_transaction(
#         nonce=0,
#         gas_price=10,
#         gas=100000,
#         to=dest_addr,
#         value=to_wei('0.5', 'ether'),
#         data=b'',
#     )
#     tx = tx.as_signed_transaction(address1_private_key)
#
#     receipt, computation = vm.apply_transaction(base_block.header, tx)
#     vm.state.persist()

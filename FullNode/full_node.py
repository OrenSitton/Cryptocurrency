"""
Author: Oren Sitton
File: Full Node.py
Python Version: 3
Description:
"""

import logging
import queue
import socket
import threading
import datetime
from hashlib import sha256
from time import sleep
import select
import pickle
from Dependencies import Blockchain
from Dependencies import SyncedArray
from Dependencies import Transaction
from Dependencies import Flags
from Crypto.Hash import SHA256
from Crypto.PublicKey import RSA
from Crypto.Signature import PKCS1_v1_5

"""
Global Variables
----------------
client_sockets : SyncedArray
    list of current client sockets
transactions : SyncedArray
    list of pending transactions
inputs : SyncedArray
    list of current input sockets
outputs : SyncedArray
    list of current output sockets
thread_queue : queue.Queue
    queue for data returned from threads
flags : Flags
    dictionary for thread initiation / termination flags
"""

client_sockets = SyncedArray(name="client List")
transactions = SyncedArray(name="transaction List")
inputs = SyncedArray(name="input List")
outputs = SyncedArray(name="output List")
thread_queue = queue.SimpleQueue()
flags = Flags()

"""
Initiation Functions
--------------------

"""


def get_config_data(data):
    with open("Dependencies\\config.txt", "rb") as file:
        configuration = pickle.load(file)

    return configuration.get(data)


def initiate_server(ip, port):
    """
    initializes server socket object to address,
    non-blocking and to accept new connections
    :param ip: ipv4 address
    :type ip: str
    :param port: tcp port
    :type port: int
    :return: server socket
    :rtype: socket.socket
    """
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setblocking(False)
    server_socket.bind((ip, port))
    server_socket.listen(5)
    return server_socket


def initiate_client(ip, port):
    client_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    try:
        client_socket.connect((ip, port))
        logging.info("Connected to node [{}, {}]"
                     .format(client_socket.getpeername()[0], client_socket.getpeername()[1]))

    except ConnectionRefusedError:
        logging.info("Connection attempt refused [{}, {}]"
                     .format(ip, port))

    else:
        client_sockets.append(client_socket)


def initiate_clients(addresses, port):
    threads = []
    for i, address in enumerate(addresses):
        if address != get_config_data("ip_address"):
            thread = threading.Thread(name="Client Connection Thread {}".format(i + 1), target=initiate_client,
                                      args=(address, port,))
            thread.start()
            threads.append(thread)

    for thread in threads:
        thread.join()

    logging.info("{} nodes accepted connection"
                 .format(len(client_sockets)))


def seed_clients(dns_ip, dns_port, peer_port, **kwargs):
    if kwargs.get("attempts"):
        attempts = kwargs.get("attempts")
    else:
        attempts = 5

    if kwargs.get("delay"):
        delay = kwargs.get("delay")
    else:
        delay = 5

    peer_addresses = []

    try:
        seed_client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        seed_client.connect((dns_ip, dns_port))

    except ConnectionRefusedError:
        attempts = 0

    for x in range(attempts):
        seed_client.send("00001a".encode())
        data = seed_client.recv(5).decode()
        data = seed_client.recv(int(data, 16)).decode()

        if data[0] == "b":
            peer_addresses = []
            seed_client.close()
            peer_count = int(data[1:3], 16)
            data = data[3:]
            for peer in range(peer_count):
                ip_address = "{}.{}.{}.{}".format(
                    int(data[:2], 16), int(data[2:4], 16), int(data[4:6], 16), int(data[6:], 16)
                )
                print(ip_address)
                peer_addresses.append(ip_address)
                data = data[8:]
            break
        else:
            sleep(delay)

    seed_client.close()

    logging.info("Seeding yielded {} addresses".format(len(peer_addresses)))

    # add additional peers from config file
    # run in the background

    initiate_clients(peer_addresses, peer_port)


"""
Calculation Functions
---------------------

"""


def hexify(number, length):
    """
    calculates hexadecimal value of the number, with prefix zeroes to match length
    :param number: number to calculate hex value for, in base 10
    :type number: int
    :param length: requested length of hexadecimal value
    :type length: int
    :return: hexadecimal value of the number, with prefix zeroes
    :rtype: str
    :raise Exception: ValueError (message size is larger than length)
    """
    if not isinstance(number, int):
        raise TypeError("Transaction.hexify(number, length): expected number to be of type int")
    if not isinstance(length, int):
        raise TypeError("Transaction.hexify(number, length): expected length to be of type int")
    if number < 0:
        raise ValueError("Transaction.hexify(number, length): expected non-negative value for number, received {} "
                         "instead".format(number))
    if length < 0:
        raise ValueError("Transaction.hexify(number, length): expected non-negative value for length, received {} "
                         "instead".format(length))

    hex_base = hex(number)[2:]

    if len(hex_base) <= length:
        hex_base = (length - len(hex_base)) * "0" + hex_base
        return hex_base
    else:
        raise ValueError("Transaction.hexify(number, length): message size is larger than length")


def hexify_string(string):
    return string.encode("utf-8").hex()


def validate_transaction(transaction, blockchain):
    # validate inputs are in order
    for x in range(1, len(transaction.inputs)):
        if transaction.inputs[x][0] > transaction.inputs[x - 1][0]:
            return False
        elif transaction.inputs[x][0] == transaction.inputs[x - 1][0]:
            if transaction.inputs[x][1] > transaction.inputs[x - 1][1]:
                return False

    # validate outputs are in order
    for x in range(1, len(transaction.outputs)):
        if transaction.outputs[x][1] > transaction.outputs[x - 1][1]:
            return False

    # validate that keys don't appear twice
    input_lst = transaction.inputs.copy()
    for inp1 in transaction.inputs:
        input_lst.remove(inp1)
        for inp2 in input_lst:
            if inp1[0] == inp2[0]:
                return False
                # key appears twice
    output_lst = transaction.outputs.copy()
    for output1 in transaction.outputs:
        output_lst.remove(output1)
        for output2 in transaction.outputs:
            if output1[0] == output2[0]:
                return False
                # output key appears twice

    # validate sources, amounts
    input_coin_amounts = 0
    output_coin_amounts = 0
    for inp in transaction.inputs:
        block = blockchain.get_block_consensus_chain(inp[1])
        input_transaction = Transaction.from_network_format(block[8].split(",")[inp[2] - 1])

        appears_in_source = False

        for src_out in input_transaction.outputs:
            if src_out[0] == inp[0]:
                appears_in_source = True
                input_coin_amounts += src_out[1]
        if not appears_in_source:
            return False
            # input is not valid (no coin in source for requested key)

    for output in transaction.outputs:
        output_coin_amounts += output[1]

    if not output_coin_amounts - input_coin_amounts:
        return False
        # input & output amounts are not equal

    # validate signatures
    for inp in transaction.inputs:
        key = inp[0]
        key = RSA.import_key(key)

        hasher = SHA256.new(transaction.signing_format().encode("utf-8"))
        verifier = PKCS1_v1_5.new(key)
        if not verifier.verify(hasher, inp[3]):
            return False
            # signature mismatch

    return True


"""
Message Builder Functions
-------------------------
"""


def build_block_message(block):
    block_number = hexify(block[1], 6)
    if True:
        time = block[2]
        year = time[:4]
        month = time[5:7]
        day = time[8:10]
        hour = time[11:13]
        minute = time[14:16]
        second = time[17:19]
    time_stamp = datetime.datetime(year=year, month=month, day=day, hour=hour, minute=minute, second=second).timestamp()
    prev_hash = block[4]
    difficulty = hexify(block[5], 2)
    nonce = hexify(block[6], 8)
    merkle_root_hash = block[7]
    block_transactions = block[8].split(",")

    message = "d{}{}{}{}{}{}{}".format(block_number, time_stamp, difficulty, nonce, prev_hash, merkle_root_hash,
                                       hexify(len(block_transactions), 2))

    for transaction in block_transactions:
        message += hexify(len(transaction), 2) + transaction

    return message


def build_peers_message(peers_list):
    message = "b{}".format(hexify(len(peers_list), 2))
    for address in peers_list:
        address_bytes = address.split(".")
        for byte in address_bytes:
            message += hexify(int(byte), 2)
    return message


def build_error_message(error_message):
    message = "f{}".format(hexify_string(error_message))
    return message


"""
Network Protocol Functions
--------------------------

"""


def handle_peer_request_message():
    logging.info("Message is a peer request message")
    reply = build_peers_message(inputs.array)
    reply = "{}{}".format(hexify(len(reply), 5), reply)
    return reply, 1


def handle_peer_message(message):
    if len(message) < 3:
        logging.info("Message is an invalid peer message")
        return None, -1
    peer_count = int(message[1:3])
    if len(message) < 3 + 8 * peer_count:
        logging.info("Message is an invalid peer message")
        return None, -1
    logging.info("Message is a peer message")
    message = message[3:]

    addresses = []

    for x in peer_count:
        byte1 = int(message[:2], 16)
        byte2 = int(message[2:4], 16)
        byte3 = int(message[4:6], 16)
        byte4 = int(message[6:8], 16)
        address = "{}.{}.{}.{}".format(byte1, byte2, byte3, byte4)
        addresses.append(address)
        message = message[8:]
    threading.Thread(name="Peer Seeding Thread", target=initiate_clients, args=(addresses, 8333,)).start()
    return None, -1


def handle_block_request_message(message, blockchain):
    if len(message) != 71:
        # message not in correct format
        logging.info("Message is an invalid block request")
        return None, -1
    else:
        block_number = int(message[1:7], 16)
        previous_block_hash = message[7:]

        if block_number == 0 and previous_block_hash.replace("0", ""):
            # message in incorrect format
            logging.info("Message is an invalid block request")
            return None, -1
        elif block_number == 0:
            block_number = blockchain.__len__()

        # return requested block if have, else return nothing
        block = blockchain.get_block_consensus_chain(block_number, prev_hash=previous_block_hash)

        if block:
            reply = build_block_message(block)
            logging.info("Message is a block request")
            return "{}{}".format(hexify(len(reply), 5), reply), 1

        logging.info("Message is an invalid block request")
        return None, -1


def handle_block_message(message, blockchain):
    # TODO: finish implementation
    # check block validity
    # find which block it matches, remove other blocks if branch big enough
    # raise flag
    # append to database
    # if new consensus - remove transactions

    # validate minimum length
    if len(message) < 155:
        raise ValueError("handle_block_message(message): message does not represent new block")

    block_number = int(message[1:7], 16)
    posix_time = int(message[7:15], 16)
    previous_block_hash = message[15:79]
    block_difficulty = int(message[79:81], 16)
    nonce = int(message[81:89], 16)
    merkle_root_hash = message[89:153]
    transaction_count = message[153:155]
    valid = True

    # validate time created

    # validate difficulty

    # validate nonce

    maximum = 2 ** (256 - block_difficulty)
    block_hash = sha256("{}{}{}".format(block_number, previous_block_hash, nonce).encode("utf-8")).hexdigest()
    int_hash = int(block_hash, 16)

    if int_hash > maximum:
        # nonce invalid
        pass

    # validate block hash

    # validate transactions

    # validate merkle root hash

    # return appropriate message
    pass


def handle_transaction_message(message, blockchain):
    try:
        transaction = Transaction.from_network_format(message)
    except ValueError:
        logging.info("Message is an invalid transaction message")
        return None, -1
    else:
        if transaction in transactions:
            logging.info("Message is a previously received transaction message")
            return None, -1
        if validate_transaction(transaction, blockchain):
            transactions.append(transaction)
            logging.info("Message is a transaction message")
            return "{}{}".format(len(message), message), 2
        else:
            logging.info("Message is an invalid transaction message")
            return None, -1


def handle_error_message(message):
    logging.info("Message is an error message")
    return None, -1


def handle_message(message, blockchain):
    message_handling_functions = dict(a=lambda: handle_peer_request_message(),
                                      b=lambda: handle_peer_message(message),
                                      c=lambda: handle_block_request_message(message, blockchain),
                                      d=lambda: handle_block_message(message, blockchain),
                                      e=lambda: handle_transaction_message(message, blockchain),
                                      f=lambda: handle_error_message(message))

    message_type = message[:1]

    if message_type not in message_handling_functions:
        logging.info("Message is invalid (unrecognized message type)")
        reply = build_error_message("unrecognized message type")
        reply = "{}{}".format(hexify(len(reply), 5), reply)
        return reply, 1
    else:
        return message_handling_functions[message_type]


"""
Block Miner Function
--------------------
"""


def find_nonce(blockchain, difficulty, prev_id, public_key):
    """
    searches for nonce for new block to add to the blockchain
    :param blockchain: blockchain SQL connector
    :type blockchain: Blockchain
    :return: nonce
    :rtype: int
    """

    global flags
    global thread_queue

    difficulty = 0
    prev_id = 0
    public_key = 0

    prev_block_hash = blockchain[prev_id][4]
    block_number = blockchain[prev_id][1] + 1
    nonce = 0

    maximum = 2 ** (256 - difficulty)

    block_hash = sha256("{}{}{}".format(block_number, prev_block_hash, nonce).encode("utf-8")).hexdigest()
    int_hash = int(block_hash, 16)

    while int_hash > maximum and not flags["received_block_flag"]:
        nonce += 1
        block_hash = sha256("{}{}{}".format(block_number, prev_block_hash, nonce).encode("utf-8")).hexdigest()
        int_hash = int(block_hash, 16)

    if flags["received_block_flag"]:
        thread_queue.put(-1)
    else:
        flags["created_block_flag"] = True
        thread_queue.put(nonce)


def mine_new_block(blockchain):
    # TODO: implement
    pass


"""
Main Function
-------------
"""


def main():
    threading.current_thread().name = "MainNodeThread"

    global thread_queue
    global flags
    global inputs
    global outputs

    flags["received new block"] = False
    flags["created new block"] = False

    ip = get_config_data("ip address")
    port = get_config_data("port")
    seed_ip = get_config_data("seed address")
    seed_port = get_config_data("seed port")
    sql_address = get_config_data("sql address")
    sql_user = get_config_data("sql user")
    sql_password = get_config_data("sql password")

    blockchain = Blockchain(sql_address, sql_user, sql_password)

    server_socket = initiate_server(ip, port)
    logging.info("Server: Initiated [{}, {}]"
                 .format(server_socket.getsockname()[0], server_socket.getsockname()[1]))
    threading.Thread(name="Seeding Thread", target=seed_clients, args=(seed_ip, seed_port, port,)).start()

    mining_thread = threading.Thread(name="Mining Thread", target=mine_new_block, args=(blockchain,))
    mining_thread.start()

    inputs.append(server_socket)

    message_queues = {}

    while inputs:
        readable, writable, exceptional = select.select(client_sockets + inputs, outputs, client_sockets + inputs)

        for sock in readable:
            if sock is server_socket:  # new socket has connected to the server
                connection, client_address = server_socket.accept()
                connection.setblocking(False)
                inputs.append(connection)
                logging.info("[{}, {}]: New node connected".format(client_address[0], client_address[1]))

            else:
                size = sock.read(5).decode()
                if not size:
                    logging.info("[{}, {}]: Node disconnected"
                                 .format(sock.getpeername()[0], sock.getpeername()[1]))
                    if sock in inputs:
                        inputs.remove(sock)
                    else:
                        client_sockets.remove(sock)
                    if sock in outputs:
                        outputs.remove(sock)
                    if sock in message_queues:
                        del message_queues[sock]
                else:
                    size = int(size, 16)
                    message = sock.read(size)
                    logging.info("[{},{}]: Received message from node".format(sock.getpeername[0], sock.getpeername[1]))
                    reply = handle_message(message, blockchain)
                    if reply[1] == -1:
                        logging.info("[{},{}]: Message does not warrant a reply"
                                     .format(sock.getpeername[0], sock.getpeername[1]))
                    elif reply[1] == 1:
                        logging.info("[{},{}]: Replying to sending node only"
                                     .format(sock.getpeername[0], sock.getpeername[1]))
                        if sock not in message_queues:
                            message_queues[sock] = queue.SimpleQueue()
                        if sock not in outputs:
                            outputs.append(sock)
                        message_queues[sock].put(reply[0])
                    elif reply[1] == 2:
                        logging.info("[{},{}]: Replying to all connected nodes"
                                     .format(sock.getpeername[0], sock.getpeername[1]))
                        for other_sock in client_sockets + inputs:
                            if other_sock not in message_queues:
                                message_queues[other_sock] = queue.SimpleQueue()
                            if other_sock not in outputs:
                                outputs.append(other_sock)
                            message_queues[other_sock].put(reply[0])

        for sock in writable:
            if not message_queues[sock].empty():
                message = message_queues[sock].get()
                sock.send(message.encode())

        for sock in exceptional:
            if sock in inputs:
                inputs.remove(sock)
            elif sock in client_sockets:
                client_sockets.remove(sock)
            if sock in outputs:
                outputs.remove(sock)
            if sock in message_queues:
                message_queues.pop(sock)

        if flags["created new block"]:
            mining_thread.join()
            message = thread_queue.get()

            for sock in client_sockets + inputs:
                if sock not in message_queues:
                    message_queues[sock] = queue.SimpleQueue()
                if sock not in outputs:
                    outputs.append(sock)
                message_queues[sock].put(message)

            mining_thread = threading.Thread(name="Mining Thread", target=mine_new_block, args=(blockchain,))
            mining_thread.start()

        if flags["received new block"]:
            mining_thread.join()
            if not thread_queue.empty():
                thread_queue.get()
            mining_thread = threading.Thread(name="Mining Thread", target=mine_new_block, args=(blockchain,))


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG, format="%(threadName)s, %(asctime)s: %(message)s")
    main()

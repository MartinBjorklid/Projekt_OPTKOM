import heapq
from itertools import count
import numpy as np

# ============================================================
# 1. Kod för optimering av text
# ============================================================

class HuffmanNode:
    def __init__(self, frequency, char=None, left=None, right=None):
        self.frequency = frequency
        self.char = char
        self.left = left
        self.right = right

    def is_leaf(self):
        return self.char is not None


def build_huffman_tree(frequencies):

    heap = []
    counter = count()

    # Skapa en nod för varje tecken
    for char, frequency in frequencies.items():
        node = HuffmanNode(frequency, char)

        heapq.heappush(
            heap,
            (frequency, next(counter), node)
        )

    # Slå ihop de två minst frekventa noderna
    while len(heap) > 1:

        frequency1, _, left = heapq.heappop(heap)
        frequency2, _, right = heapq.heappop(heap)

        parent = HuffmanNode(
            frequency1 + frequency2,
            left=left,
            right=right
        )

        heapq.heappush(
            heap,
            (
                parent.frequency,
                next(counter),
                parent
            )
        )

    return heap[0][2]

def create_huffman_codes(node, code=None, codes=None):

    if codes is None:
        codes = {}
    if code is None:
        code = []

    # Vi har hittat ett tecken
    if node.is_leaf():
        codes[node.char] = code 
        return codes

    # Vänster = 0
    create_huffman_codes(
        node.left,
        code + [0],
        codes
    )

    # Höger = 1
    create_huffman_codes(
        node.right,
        code + [1],
        codes
    )

    return codes

# ============================================================
# 2. Kod för göra text till bit
# ============================================================
def huffman_encode(text, codes): 

    result = []

    for char in text:

        if char not in codes:
            raise ValueError(
                f"Tecknet {repr(char)} finns inte i frekvenstabellen."
            )

        result += codes[char]

    return [int(bit) for bit in result] ##ta bort list om en lång sträng

def encode(compressed_bit):
    """
    Encodes compressed bits using hamming code (7,4) as in example
    \nadds padding to become divisible by 4
    \ncreates generator matrix G and parity check matrix H

    Parameters
    ----------
    compressed_bit : list
        String containing compressed binary data

    Returns
    -------
    encoded_bits : np.ndarray
        Binary array containing Hamming encoded bits
    H : np.ndarray
        Parity check matrix used when decoding
    padding : int
        Number of padding bits added before encoding
    """
    #Adding padding
    compressed_bit = list(compressed_bit)
    padding = 0
    remainder = len(compressed_bit)%4
    while remainder != 0:
        #print(remainder)
        compressed_bit.append(0)
        remainder = len(compressed_bit)%4
        padding += 1
    bit_array = np.array(list(compressed_bit), dtype=int).reshape(-1,4)

    #Creating H and G matrixes from example
    P = np.array([
        [1,1,0],
        [1,0,1],
        [0,1,1],
        [1,1,1]], dtype=int)
    I_r = np.eye(3, dtype=int)
    I_k = np.eye(4, dtype=int)
    H = np.concatenate((P.T,I_r),axis=1)
    G = np.concatenate((I_k,P), axis=1)
    #Encoding
    encoded_matrix = (bit_array@G)%2
    encoded_bits = encoded_matrix.flatten()
    #Matches theory as follows (k = 4, n = 7):
    #print(len(compressed_bit)/len(encoded_bits))
    #print(4/7)
    return encoded_bits, H, padding


def decode(encoded_bits, H, padding):
    """
    Decodes and error corrects data that has been coded with hamming code (7,4)
    \nCalculates syndrome and corrects if wrong (1 bit only)
    \nremoves parity bits and padding after decoding and error correction.

    Parameters
    ----------
    encoded_bits : np.ndarray
        Received binary data after demodulation
    H : np.ndarray
        Parity check matrix
    padding : int
        Number of padding bits added during encoding

    Returns
    -------
    decoded_data : list
        Integer bits with parity and padding removed, ready for huffman_decode
    """
    #r has shape (900/4,7)
    r = np.asarray(encoded_bits, dtype=int).reshape(-1,7).copy()
    #s has shape (900/4,3)
    s = ((H@r.T)%2).T
    #print(s)
    H_columns = H.T
    error_index = []
    for block_index,syndrome in enumerate(s):
        if syndrome.any():
            for seq_index,col in enumerate(H_columns):
                if np.array_equal(syndrome,col):
                    #print(seq_index)
                    error_index.append([block_index,seq_index])
                    print(f"Error fixed at row,col {block_index,seq_index}")
                    r[block_index,seq_index] ^= 1
                    break
    k = r[:,:4]
    decoded_data = k.ravel().tolist()
    #print(decoded_data)
    if padding > 0:
        decoded_data = decoded_data[:-padding]
    return decoded_data


# ============================================================
# 3. Gör bits till text
# ============================================================

def huffman_decode(bits, tree):

    result = []
    node = tree

    for bit in bits:

        if bit == 0:
            node = node.left

        elif bit == 1:
            node = node.right

        else:
            raise ValueError(
                "Bitsträngen får endast innehålla 0 och 1."
            )

        # Vi har nått ett tecken
        if node.is_leaf():

            result.append(node.char)

            # Börja om från roten
            node = tree

    # Om vi inte är tillbaka vid roten är bitsträngen
    # ofullständig
    if node != tree:
        raise ValueError(
            "Ofullständig Huffman-bitsträng."
        )

    return "".join(result)


# ============================================================
# 6. SVENSK FREKVENSTABELL
# ============================================================

frequencies = {

    " ": 18.0,

    "e": 12.0,
    "a": 9.3,
    "n": 8.5,
    "r": 8.2,
    "t": 7.5,
    "s": 6.5,
    "i": 6.2,
    "l": 5.3,
    "o": 5.1,
    "d": 4.5,
    "m": 3.4,
    "k": 3.1,
    "g": 3.0,
    "v": 2.4,
    "h": 2.1,
    "u": 2.0,

    "b": 1.3,
    "c": 1.7,
    "f": 1.8,
    "p": 1.8,

    "å": 1.3,
    "ä": 1.8,
    "ö": 1.0,

    "j": 0.7,
    "y": 0.7,

    "q": 0.02,
    "w": 0.02,
    "x": 0.1,
    "z": 0.1,
    "!": 0.1,
    "?": 0.1
}


tree = build_huffman_tree(frequencies)

codes = create_huffman_codes(tree)


# ============================================================
# 8. VISA BIT-TABELLEN
# ============================================================

# print("BIT-TABELL")
# print("-------------------------")

# for char, code in sorted(
#     codes.items(),
#     key=lambda item: (len(item[1]), item[1])
# ):
#     print(repr(char), "->", code)


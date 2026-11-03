from pathlib import Path
import numpy as np

# Träningstexten som Huffmanträdet byggs från (ligger bredvid denna fil)
TRANINGSTEXT = Path(__file__).with_name("training-text.txt")

# ============================================================
# 1. Huffmanträd från träningstext
# ============================================================

def huffman(x):
    with open(x, encoding="utf-8") as f: training_text = f.read()

    ant_symboler = {}
    for symbol in training_text:
        ant_symboler[symbol] = ant_symboler.get(symbol,0)+1

    noder = [(freq,symbol) for symbol, freq in ant_symboler.items()]

    while len(noder)>1:
        noder.sort(key=lambda nod : nod[0])
        v_freq,v_tree = noder.pop(0)
        h_freq,h_tree = noder.pop(0)
        noder.append((v_freq+h_freq,(v_tree,h_tree)))

    huffman_trad = noder[0][1]
    return huffman_trad


def bygg_kodbok(trad,prefix="",kodbok=None):
    if kodbok is None:
        kodbok = {}
    if isinstance(trad,str):
        kodbok[trad]=prefix
    else:
        v,h=trad
        bygg_kodbok(v,prefix+"0",kodbok)
        bygg_kodbok(h,prefix+"1",kodbok)
    return kodbok

# ============================================================
# 2. Kod för göra text till bit
# ============================================================
def huffman_encode(txt,kodbok):
    for symbol in txt:
        if symbol not in kodbok:
            raise ValueError(
                f"Tecknet {repr(symbol)} finns inte i träningstexten."
            )
    # Lista med heltal 0/1 eftersom resten av kedjan (Hamming, sändning) vill ha det
    return [int(bit) for symbol in txt for bit in kodbok[symbol]]

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

def huffman_decode(bitar,trad):
    avkodad = []
    nod = trad
    for bit in bitar:
        nod = nod[0] if int(bit) == 0 else  nod[1]
        if isinstance(nod,str):
            avkodad.append(nod)
            nod = trad
    return "".join(avkodad)


tree = huffman(TRANINGSTEXT)

codes = bygg_kodbok(tree)

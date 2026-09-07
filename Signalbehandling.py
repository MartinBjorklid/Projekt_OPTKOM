import 
import
import


def readfile(filename):
    return a string or smth


def hoffman(string) #Kolla frekvens och koda till tal i ökande ordning

def get_binary(x: int) -> str:       #gör om talen till binär       
    """ Ex3: Returns the binary representation of x """
    if x<0:
        return "-" + get_binary(-x) #Om det är ett negativt tall, gör det för det positiva
    
    if x==0:
        return "0" #Om x är noll så blir det noll
    if x==1:
        return "1" #Om x är ett så blir det en etta
    
    return get_binary(x//2) + str(x%2) #Kör talet heltal delat på 2, skriv ut det som blir resten, en etta ellet nolla

    pass


def to_bin(strin or smth):
    return binary_number #gör till en utdata



if __name__ == '__main__':
    readfile(filename=)

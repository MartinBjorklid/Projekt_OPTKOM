


class BST:

    class Node:
        def __init__(self, key, left=None, right=None):
            self.key = key
            self.left = left
            self.right = right

        def __iter__(self):     # Discussed in the text on generators
            if self.left:
                yield from self.left
            yield self.key
            if self.right:
                yield from self.right

    def __init__(self, root=None):
        self.root = root

    def __iter__(self):         # Dicussed in the text on generators
        if self.root:
            yield from self.root

    def insert(self, key):
        self.root = self._insert(self.root, key)

    def _insert(self, r, key):
        if r is None:
            return self.Node(key)
        elif key < r.key:
            r.left = self._insert(r.left, key)
        elif key > r.key:
            r.right = self._insert(r.right, key)
        else:
            pass  # Already there
        return r

    def print(self):
        self._print(self.root)

    def _print(self, r):
        if r:
            self._print(r.left)
            print(r.key, end=' ')
            self._print(r.right)

    def contains(self, k): # given function
        n = self.root
        while n and n.key != k:
            if k < n.key:
                n = n.left
            else:
                n = n.right
        return n is not None

    def contains(self, k): #Ex8: write recursive contains
        return self._contains(self.root,k)
    
    def _contains(self,node,k):
        if node is None: #om det inte finns någon node så finns även inte k
            return False
        if node.key == k: # om nyckeln är k så är det sant
            return True
        elif k < node.key: #om k är mindre än nyckeln, sök till vänster
            return self._contains(node.left,k)
        elif k>node.key: #om k är större än k, sök till höger
            return self._contains(node.right,k)
        else: return False #hit kommer vi då varken k är mindre, större eller nyckeln.

   def to_list(self):                      #   Ex11   
        
        lst = [] #skapa en tom lista
        if self.root is None:
            return lst #om deti nte finns något retunera den tomma listan
        
        for x in self: #för alla x, lägg till den i listan
            lst.append(x)
        return lst

  def height(self):                 #        Ex9     
        return self._height(self.root)
    
    def _height(self,r): #om r är inget så är höjden noll
        if r is None: 
            return 0 #om r inte finn finns inte någon höjd
        
        return 1 + max(self._height(r.left) , self._height(r.right)) #Om r inte är noll, kolla vilket av vänster och höger som är längst och returnera max höjden.
        
        pass
        
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



if __name__ == "__main__":
  


  
    
     

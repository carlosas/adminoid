"""Read Java constant pools/method bytecode without executing installed game code."""
import struct

def parse(data):
    pos=8
    def u2():
        nonlocal pos
        v=struct.unpack_from('>H',data,pos)[0];pos+=2;return v
    def u4():
        nonlocal pos
        v=struct.unpack_from('>I',data,pos)[0];pos+=4;return v
    count=u2();pool=[None]*count;i=1
    while i<count:
        tag=data[pos];pos+=1
        if tag==1:
            n=u2();pool[i]=data[pos:pos+n].decode('utf-8',errors='replace');pos+=n
        elif tag in (3,4): pool[i]=struct.unpack_from('>i' if tag==3 else '>f',data,pos)[0];pos+=4
        elif tag in (5,6):pos+=8;i+=1
        elif tag in (7,8,16,19,20):pool[i]=(tag,u2())
        elif tag in (9,10,11,12,17,18):pool[i]=(tag,u2(),u2())
        elif tag==15:pool[i]=(tag,data[pos],struct.unpack_from('>H',data,pos+1)[0]);pos+=3
        else:raise ValueError('Unsupported Java format.')
        i+=1
    def attrs():
        nonlocal pos
        out={}
        for _ in range(u2()):
            name=pool[u2()];length=u4();out[name]=data[pos:pos+length];pos+=length
        return out
    pos+=6
    n=u2();pos+=n*2
    for _ in range(u2()):pos+=6;attrs()
    methods={}
    for _ in range(u2()):
        flags=u2();name=pool[u2()];desc=pool[u2()];a=attrs()
        if 'Code' in a:
            code=a['Code'];length=struct.unpack_from('>I',code,4)[0];methods[name+desc]=code[8:8+length]
    return pool,methods

def packaged_version(jar):
    import zipfile
    with zipfile.ZipFile(jar) as z:
        pool, methods = parse(z.read('zombie/core/Core.class'))
    code=methods.get('<clinit>()V',b'')
    # Core's static initializer constructs the immutable GameVersion first.
    if len(code)<6 or code[0]!=0xbb or code[3]!=0x59: raise ValueError('Unable to verify the packaged version.')
    cls=pool[int.from_bytes(code[1:3],'big')]
    if not isinstance(cls,tuple) or pool[cls[1]]!='zombie/core/GameVersion': raise ValueError('Unknown version initializer.')
    pos=4;values=[]
    for _ in range(3):
        op=code[pos];pos+=1
        if 0x02<=op<=0x08: value=op-3
        elif op==0x10: value=int.from_bytes(code[pos:pos+1],'big',signed=True);pos+=1
        elif op==0x11: value=int.from_bytes(code[pos:pos+2],'big',signed=True);pos+=2
        elif op in (0x12,0x13):
            n=1 if op==0x12 else 2;idx=int.from_bytes(code[pos:pos+n],'big');pos+=n;value=pool[idx]
            if isinstance(value,tuple) and value[0]==8:value=pool[value[1]]
        else:raise ValueError('Unable to verify the version before starting.')
        values.append(value)
    if code[pos]!=0xb7:raise ValueError('Unknown version constructor.')
    ctor=pool[int.from_bytes(code[pos+1:pos+3],'big')]
    name_type=pool[ctor[2]]
    if pool[name_type[1]]!='<init>' or pool[name_type[2]] not in ('(IILjava/lang/String;)V','(III)V'):
        raise ValueError('Unknown version signature.')
    if not all(type(v) is int for v in values[:2]) or not isinstance(values[2],(int,str)):raise ValueError('Unrecognized version.')
    return '.'.join(str(v) for v in values[:2]) + (str(values[2]) if isinstance(values[2],str) else '.'+str(values[2]))

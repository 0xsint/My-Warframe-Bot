from cryptography.fernet import Fernet

key = Fernet.generate_key()
print ("WFM_ENCRYPT_KEY=\"" + key.decode() + "\"")
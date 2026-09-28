from dotenv import load_dotenv 
load_dotenv() 
import os, time 
from groq import Groq 
c = Groq(api_key=os.getenv('GROQ_API_KEY')) 
print('key loaded:', bool(os.getenv('GROQ_API_KEY'))) 
t = time.time() 
r = c.chat.completions.create(model='openai/gpt-oss-120b', messages=[{'role':'user','content':'what is UPICON'}]) 
print(r.choices[0].message.content) 
print('took', time.time()-t, 'seconds')
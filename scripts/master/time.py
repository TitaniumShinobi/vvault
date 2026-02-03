import datetime

def get_current_time():
    """Fetches the current time in a readable format."""
    now = datetime.datetime.now()
    return now.strftime("%Y-%m-%d %H:%M:%S")

if __name__ == "__main__":
    print(get_current_time())
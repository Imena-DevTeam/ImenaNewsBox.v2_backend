# This scripts is to test for which TLS flag a provider uses e.g "gmail"

import asyncio
from email.message import EmailMessage

import aiosmtplib

USER = "youraddress@gmail.com"
PASSWORD = "paste-the-16-char-google-app-password"


async def main():
    m = EmailMessage()
    m["From"] = USER
    m["To"] = USER
    m["Subject"] = "smtp test"
    m.set_content("hello")
    print(
        await aiosmtplib.send(
            m,
            hostname="smtp.gmail.com",
            port=465,
            use_tls=True,
            start_tls=False,
            username=USER,
            password=PASSWORD,
            timeout=15,
        )
    )


asyncio.run(main())

from utils.network.headless import HeadlessBrowser
from utils.network.socket import Socket
from utils.logging.log import Log
from utils.type.dynamic import DynamicObject

from database.session import Session
from database.engine import Engine
from database.models import Domain

from pipeline.elastic import Elastic
from pipeline.elastic.documents import Webpage, Service, Port

from datetime import datetime
from urllib.parse import urlparse
from io import BytesIO

import pipeline.source as pipelines

import boto3
import os


class Crawler:
    """
    DarkLight onion domain crawler.
    """
    def __init__(self, ini):
        Log.i("Starting crawler")
        self.ini = ini

    def scan(self, url):
        """Scan and crawl url which user requested."""
        Log.i("Trying to crawl {} url".format(url))

        domain = urlparse(url).netloc
        obj = DynamicObject()

        # Step 1. Visit website using headless tor browser
        Log.d("Step 1. Visiting {} website using headless browser".format(url))

        browser = HeadlessBrowser(
            ini=self.ini,
            tor_network=True
        )

        report = browser.run(url)

        del browser

        # if browser have an exception return from here
        if not report:
            return obj

        obj.webpage = report

        # Step 2. Scan common service port
        Log.d("Step 2. Scanning {} domain's common service port".format(domain))
        obj.port = self._portscan(domain)

        # Step 3. TO-DO

        return obj

    def _portscan(self, domain):
        """Scan and check opened port."""
        socket = Socket(
            tor_network=True,
            ini=self.ini,
        )

        # common service port list
        services = [
            {'number': 20, 'status': False},
            {'number': 21, 'status': False},
            {'number': 22, 'status': False},
            {'number': 23, 'status': False},
            {'number': 25, 'status': False},
            {'number': 80, 'status': False},
            {'number': 110, 'status': False},
            {'number': 123, 'status': False},  # NTP
            {'number': 143, 'status': False},
            {'number': 194, 'status': False},  # IRC
            {'number': 389, 'status': False},
            {'number': 443, 'status': False},
            {'number': 993, 'status': False},  # IMAPS
            {'number': 3306, 'status': False},
            {'number': 3389, 'status': False},
            {'number': 5222, 'status': False}, # XMPP
            {'number': 6667, 'status': False}, # Public IRC
            {'number': 8060, 'status': False}, # OnionCat
            {'number': 8333, 'status': False}, # Bitcoin
        ]

        for i in range(len(services)):
            opened = socket.ping_check(domain, services[i]['number'])
            services[i]['status'] = opened
            Log.d("{} port is {}".format(
                services[i]['number'], 'opened' if opened else 'closed'
            ))

        del socket

        return services

    def save(self, id, obj):
        """Save crawled data into database."""
        Log.i("Saving crawled data")

        meta = {
            'id': id,
        }

        engine = Engine.create(ini=self.ini)

        with Session(engine=engine) as session:
            domain = session.query(Domain).filter_by(uuid=id).first()

        engine.dispose()

        # pass the pipeline before saving data (for preprocessing)
        for pipeline in pipelines.__all__:
            _class = pipeline(domain, data=obj, ini=self.ini)

            if _class.active:
                Log.d(f"handling the {_class.name} pipeline")
                try:
                    _class.handle()
                except:
                    Log.e(f"Error while handling {_class.name} pipeline")
            else:
                Log.d(f"{_class.name} pipeline isn't active")

            del _class

        with Elastic(ini=self.ini):
            # upload screenshot at Amazon S3
            screenshot = self.upload_screenshot(obj.webpage.screenshot, id)

            Webpage(
                meta=meta,
                url=obj.webpage.url,
                domain=obj.webpage.domain,
                title=obj.webpage.title,
                time=datetime.now(),
                source=obj.webpage.source,
                screenshot=screenshot,
                language=obj.webpage.language,
                headers=obj.webpage.headers,
                tree=obj.webpage.tree,
            ).save()

            Port(
                meta=meta,
                services=[
                    Service(number=port['number'], status=port['status']) for port in obj.port]
            ).save()

    def upload_screenshot(self, screenshot, id):
        """Upload screenshot into S3 storage or local storage."""
        bucket = self.ini.read('STORAGE', 'BUCKET_NAME')
        key = f'screenshot/{id}.jpg'

        # if user want to upload screenshot into s3 storage
        if bucket:
            client = boto3.client(service_name='s3',
                                  region_name=self.ini.read('STORAGE', 'REGION_NAME'),
                                  aws_access_key_id=self.ini.read('STORAGE', 'AWS_ACCESS_KEY_ID'),
                                  aws_secret_access_key=self.ini.read('STORAGE', 'AWS_SECRET_ACCESS_KEY'))

            client.upload_fileobj(BytesIO(screenshot),
                                  Bucket=bucket,
                                  Key=key,
                                  ExtraArgs={'ACL': 'public-read'})

            return f"{client.meta.endpoint_url}/{bucket}/{key}"
        else:
            if not os.path.exists('screenshot'):
                os.mkdir('screenshot')

            with open(key, 'wb') as f:
                f.write(screenshot)

            return key

    def __del__(self):
        Log.i("Ending crawler")
        del self

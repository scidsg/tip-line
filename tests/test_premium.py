from flask.testing import FlaskClient

from hushline.db import db
from hushline.model import StripeEvent


def test_webhook_customer_subscription_created(client: FlaskClient) -> None:
    pass


def test_webhook_customer_subscription_updated(client: FlaskClient) -> None:
    pass


def test_webhook_customer_subscription_deleted(client: FlaskClient) -> None:
    pass


def test_webhook_invoice_created(client: FlaskClient) -> None:
    pass


def test_webhook_invoice_payment_succeeded(client: FlaskClient) -> None:
    pass

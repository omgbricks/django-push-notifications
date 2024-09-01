import asyncio
import time
from dataclasses import asdict, dataclass
from typing import Awaitable, Callable, Dict, Optional, Union

from aioapns import APNs, ConnectionError, NotificationRequest, PushType
from aioapns.common import NotificationResult

from . import models
from .conf import get_manager
from .exceptions import APNSServerError

ErrFunc = Optional[Callable[[NotificationRequest, NotificationResult], Awaitable[None]]]
"""function to proces errors from aioapns send_message"""


class NotSet:
	def __init__(self):
		raise RuntimeError("NotSet cannot be instantiated")


class Credentials:
	pass


@dataclass
class TokenCredentials(Credentials):
	key: str
	key_id: str
	team_id: str


@dataclass
class CertificateCredentials(Credentials):
	client_cert: str


@dataclass
class Alert:
	"""
	The information for displaying an alert. A dictionary is recommended. If you specify a string, the alert displays your string as the body text.

	https://developer.apple.com/documentation/usernotifications/setting_up_a_remote_notification_server/generating_a_remote_notification
	"""

	title: str = NotSet
	"""
	The title of the notification. Apple Watch displays this string in the short look notification interface. Specify a string that’s quickly understood by the user.
	"""

	subtitle: str = NotSet
	"""
	Additional information that explains the purpose of the notification.
	"""

	body: str = NotSet
	"""
	The content of the alert message.
	"""

	launch_image: str = NotSet
	"""
	The name of the launch image file to display. If the user chooses to launch your app, the contents of the specified image or storyboard file are displayed instead of your app’s normal launch image.
	"""

	title_loc_key: str = NotSet
	"""
	The key for a localized title string. Specify this key instead of the title key to retrieve the title from your app’s Localizable.strings files. The value must contain the name of a key in your strings file
	"""

	title_loc_args: list[str] = NotSet
	"""
	An array of strings containing replacement values for variables in your title string. Each %@ character in the string specified by the title-loc-key is replaced by a value from this array. The first item in the array replaces the first instance of the %@ character in the string, the second item replaces the second instance, and so on.
	"""

	subtitle_loc_key: str = NotSet
	"""
	The key for a localized subtitle string. Use this key, instead of the subtitle key, to retrieve the subtitle from your app’s Localizable.strings file. The value must contain the name of a key in your strings file.
	"""

	subtitle_loc_args: list[str] = NotSet
	"""
	An array of strings containing replacement values for variables in your title string. Each %@ character in the string specified by subtitle-loc-key is replaced by a value from this array. The first item in the array replaces the first instance of the %@ character in the string, the second item replaces the second instance, and so on.
	"""

	loc_key: str = NotSet
	"""
	The key for a localized message string. Use this key, instead of the body key, to retrieve the message text from your app’s Localizable.strings file. The value must contain the name of a key in your strings file.
	"""

	loc_args: list[str] = NotSet
	"""
	An array of strings containing replacement values for variables in your message text. Each %@ character in the string specified by loc-key is replaced by a value from this array. The first item in the array replaces the first instance of the %@ character in the string, the second item replaces the second instance, and so on.
	"""

	sound: Union[str, any] = NotSet
	"""
	string
	The name of a sound file in your app’s main bundle or in the Library/Sounds folder of your app’s container directory. Specify the string “default” to play the system sound. Use this key for regular notifications. For critical alerts, use the sound dictionary instead. For information about how to prepare sounds, see UNNotificationSound.

	dictionary
	A dictionary that contains sound information for critical alerts. For regular notifications, use the sound string instead.
	"""

	def asDict(self) -> dict[str, any]:
		python_dict = asdict(self)
		return {
			key.replace("_", "-"): value
			for key, value in python_dict.items()
			if value is not NotSet
		}


class APNsService:
	__slots__ = ("client",)

	def __init__(
		self,
		application_id: str = None,
		creds: Credentials = None,
		topic: str = None,
		err_func: ErrFunc = None,
	):
		try:
			loop = asyncio.get_event_loop()
		except RuntimeError:
			loop = asyncio.new_event_loop()
			asyncio.set_event_loop(loop)

		self.client = self._create_client(
			creds=creds, application_id=application_id, topic=topic, err_func=err_func
		)

	def send_message(
		self,
		request: NotificationRequest,
	):
		loop = asyncio.get_event_loop()
		routine = self.client.send_notification(request)
		res = loop.run_until_complete(routine)
		return res

	def _create_notification_request_from_args(
		self,
		registration_id: str,
		alert: Union[str, Alert],
		badge: int = None,
		sound: str = None,
		extra: dict = {},
		expiration: int = None,
		thread_id: str = None,
		loc_key: str = None,
		priority: int = None,
		collapse_id: str = None,
		aps_kwargs: dict = {},
		message_kwargs: dict = {},
		notification_request_kwargs: dict = {},
	):

		push_type = PushType.ALERT

		if alert is None:
			alert = Alert(body="")
			push_type = PushType.BACKGROUND

		if loc_key:
			if isinstance(alert, str):
				alert = Alert(body=alert)
			alert.loc_key = loc_key

		if isinstance(alert, Alert):
			alert = alert.asDict()

		notification_request_kwargs_out = notification_request_kwargs.copy()
                notification_request_kwargs_out["push_type"] = push_type

		if expiration is not None:
			notification_request_kwargs_out["time_to_live"] = expiration - int(
				time.time()
			)
		if priority is not None:
			notification_request_kwargs_out["priority"] = priority

		if collapse_id is not None:
			notification_request_kwargs_out["collapse_key"] = collapse_id

		request = NotificationRequest(
			device_token=registration_id,
			message={
				"aps": {
					"alert": alert,
					"badge": badge,
					"sound": sound,
					"thread-id": thread_id,
					**aps_kwargs,
				},
				**extra,
				**message_kwargs,
			},
			**notification_request_kwargs_out,
		)

		return request

	def _create_client(
		self,
		creds: Credentials = None,
		application_id: str = None,
		topic=None,
		err_func: ErrFunc = None,
	) -> APNs:
		use_sandbox = get_manager().get_apns_use_sandbox(application_id)
		if topic is None:
			topic = get_manager().get_apns_topic(application_id)
		if creds is None:
			creds = self._get_credentials(application_id)

		client = APNs(
			**asdict(creds),
			topic=topic,  # Bundle ID
			use_sandbox=use_sandbox,
			err_func=err_func,
		)
		return client

	def _get_credentials(self, application_id):
		if not get_manager().has_auth_token_creds(application_id):
			# TLS certificate authentication
			cert = get_manager().get_apns_certificate(application_id)
			return CertificateCredentials(
				client_cert=cert,
			)
		else:
			# Token authentication
			keyPath, keyId, teamId = get_manager().get_apns_auth_creds(application_id)
			# No use getting a lifetime because this credential is
			# ephemeral, but if you're looking at this to see how to
			# create a credential, you could also pass the lifetime and
			# algorithm. Neither of those settings are exposed in the
			# settings API at the moment.
			return TokenCredentials(key=keyPath, key_id=keyId, team_id=teamId)


# Public interface


def apns_send_message(
	registration_id: str,
	alert: Union[str, Alert],
	application_id: str = None,
	creds: Credentials = None,
	topic: str = None,
	badge: int = None,
	sound: str = None,
	extra: dict = {},
	expiration: int = None,
	thread_id: str = None,
	loc_key: str = None,
	priority: int = None,
	collapse_id: str = None,
	err_func: ErrFunc = None,
):
	"""
	Sends an APNS notification to a single registration_id.
	If sending multiple notifications, it is more efficient to use
	apns_send_bulk_message()

	Note that if set alert should always be a string. If it is not set,
	it won"t be included in the notification. You will need to pass None
	to this for silent notifications.


	:param registration_id: The registration_id of the device to send to
	:param alert: The alert message to send
	:param application_id: The application_id to use
	:param creds: The credentials to use
	"""

	try:
		apns_service = APNsService(
			application_id=application_id, creds=creds, topic=topic, err_func=err_func
		)

		request = apns_service._create_notification_request_from_args(
			registration_id,
			alert,
			badge=badge,
			sound=sound,
			extra=extra,
			expiration=expiration,
			thread_id=thread_id,
			loc_key=loc_key,
			priority=priority,
			collapse_id=collapse_id,
		)
		res = apns_service.send_message(request)
		if not res.is_successful:
			if res.description == "Unregistered":
				models.APNSDevice.objects.filter(
					registration_id=registration_id
				).update(active=False)
			raise APNSServerError(status=res.description)
	except ConnectionError as e:
		raise APNSServerError(status=e.__class__.__name__)


def apns_send_bulk_message(
	registration_ids: list[str],
	alert: Union[str, Alert],
	content_available: int = 0,
	application_id: str = None,
	creds: Credentials = None,
	topic: str = None,
	badge: int = None,
	sound: str = None,
	extra: dict = {},
	expiration: int = None,
	thread_id: str = None,
	loc_key: str = None,
	priority: int = None,
	collapse_id: str = None,
	err_func: ErrFunc = None,
):
	"""
	Sends an APNS notification to one or more registration_ids.
	The registration_ids argument needs to be a list.

	Note that if set alert should always be a string. If it is not set,
	it won"t be included in the notification. You will need to pass None
	to this for silent notifications.

	:param registration_ids: A list of the registration_ids to send to
	:param alert: The alert message to send
	:param application_id: The application_id to use
	:param creds: The credentials to use
	"""

	topic = get_manager().get_apns_topic(application_id)
	results: Dict[str, str] = {}
	inactive_tokens = []
	apns_service = APNsService(
		application_id=application_id, creds=creds, topic=topic, err_func=err_func
	)
	for registration_id in registration_ids:
		request = apns_service._create_notification_request_from_args(
			registration_id,
			alert,
			badge=badge,
			sound=sound,
			extra=extra,
			expiration=expiration,
			thread_id=thread_id,
			loc_key=loc_key,
			priority=priority,
			collapse_id=collapse_id,
			aps_kwargs={'content-available': content_available}
		)

		result = apns_service.send_message(request)
		results[registration_id] = (
			"Success" if result.is_successful else result.description
		)
		if not result.is_successful and result.description == "Unregistered":
			inactive_tokens.append(registration_id)

	if len(inactive_tokens) > 0:
		models.APNSDevice.objects.filter(registration_id__in=inactive_tokens).update(
			active=False
		)
	return results

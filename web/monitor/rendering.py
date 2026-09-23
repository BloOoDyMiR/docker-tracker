from django.contrib.messages import get_messages
from django.middleware.csrf import get_token
from django.shortcuts import render


def render_page(request, template_name, context=None):
    page = dict(context or {})
    page["csrf_token"] = get_token(request)
    page["user"] = request.user
    page["messages"] = [str(message) for message in get_messages(request)]
    return render(request, template_name, page)

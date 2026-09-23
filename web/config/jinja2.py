def environment(**options):
    from django.templatetags.static import static
    from jinja2 import Environment

    env = Environment(**options)
    env.globals.update(
        {
            "static": static,
            "url": url,
        }
    )
    return env


def url(viewname, *args, **kwargs):
    from django.urls import reverse

    return reverse(viewname, args=args, kwargs=kwargs)

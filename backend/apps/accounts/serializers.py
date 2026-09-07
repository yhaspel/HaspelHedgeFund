from django.contrib.auth import get_user_model
from rest_framework import serializers

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        # ``is_staff`` is exposed (read-only) so the UI can hide staff-only
        # affordances — e.g. the Settings › Models catalog buttons, which the
        # API refuses for non-staff — instead of rendering them and 403ing.
        fields = ("id", "email", "date_joined", "is_staff")
        read_only_fields = fields


class SignupSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ("email", "password")

    def validate_password(self, value: str) -> str:
        """Run Django's AUTH_PASSWORD_VALIDATORS at the API boundary.

        DRF does not apply them automatically, so without this the configured
        validators only guard `manage.py createsuperuser` and the admin — the
        one place passwords are actually chosen (signup) accepted "password".
        """
        from django.contrib.auth.password_validation import validate_password
        from django.core.exceptions import ValidationError as DjangoValidationError

        try:
            validate_password(value, user=User(email=self.initial_data.get("email", "")))
        except DjangoValidationError as exc:
            raise serializers.ValidationError(list(exc.messages)) from exc
        return value

    def create(self, validated_data: dict) -> object:
        return User.objects.create_user(
            email=validated_data["email"],
            password=validated_data["password"],
        )
